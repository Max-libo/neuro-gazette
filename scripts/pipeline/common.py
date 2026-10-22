"""Общие константы, пути и утилиты для пайплайна Нейрогазеты."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from entities import directory as entity_directory, normalize_entities

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Пути ─────────────────────────────────────────────────────────────────────
REPO_ROOT    = Path(__file__).parent.parent.parent
DATA_DIR     = REPO_ROOT / "docs" / "data"
INDEX_FILE   = DATA_DIR / "index.json"
LATEST_FILE  = DATA_DIR / "latest.json"
SOURCES_FILE = REPO_ROOT / "sources.yaml"

# ── Временное окно ────────────────────────────────────────────────────────────
MSK         = timezone(timedelta(hours=3))
NOW         = datetime.now(MSK)
# NEUROGAZETTE_DATE=YYYY-MM-DD — догон пропущенных выпусков из кэша.
_override_date = os.environ.get("NEUROGAZETTE_DATE", "").strip()
if _override_date:
    _od = datetime.strptime(_override_date, "%Y-%m-%d").replace(tzinfo=MSK)
    _window_end = _od.replace(hour=7, minute=0, second=0, microsecond=0)
else:
    _today_0700 = NOW.replace(hour=7, minute=0, second=0, microsecond=0)
    _window_end = _today_0700 if NOW >= _today_0700 else _today_0700 - timedelta(days=1)
CUTOFF_24H      = (_window_end - timedelta(hours=24)).astimezone(timezone.utc)
TODAY           = _window_end.date()
TODAY_STR       = TODAY.isoformat()
CUTOFF_DATE_STR = (TODAY - timedelta(days=1)).isoformat()
_cutoff_msk     = CUTOFF_24H.astimezone(MSK)
WINDOW_STR      = (
    f"{_cutoff_msk.strftime('%Y-%m-%d %H:%M')} МСК"
    f" — {_window_end.strftime('%Y-%m-%d %H:%M')} МСК"
)

SECTIONS  = ["models", "platforms", "industry", "hype"]
RAW_LIMIT = 200


# ── Источники ─────────────────────────────────────────────────────────────────
def load_sources() -> dict:
    with open(SOURCES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Фильтрация URL ────────────────────────────────────────────────────────────
_ALWAYS_VAGUE_URL = re.compile(
    r"/(?:tags?|categories?|topics?|labels?|sections?)/", re.IGNORECASE
)
_VAGUE_INDEX_URL = re.compile(r"/(?:blog|news|articles?)/?$", re.IGNORECASE)
_AGGREGATE_URL   = re.compile(
    r"/(?:latest|roundup|digest|weekly|daily|this-week|updates?)(?:[/-]|$)",
    re.IGNORECASE,
)


def is_vague_url(url: str) -> bool:
    if not url or not url.startswith("http"):
        return True
    path = urlparse(url.split("?")[0]).path.rstrip("/")
    if not path or path == "/":
        return True
    if _ALWAYS_VAGUE_URL.search(path):
        return True
    if _VAGUE_INDEX_URL.search(path):
        return True
    if _AGGREGATE_URL.search(path):
        return True
    return False


_URL_DATE_YMD = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")
_URL_DATE_YM  = re.compile(r"/(\d{4})/(\d{2})/[^/\d]")


def extract_date_from_url(url: str) -> datetime | None:
    m = _URL_DATE_YMD.search(url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            pass
    m = _URL_DATE_YM.search(url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    value = re.sub(r"\.\d+", "", value.strip())
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


# ── История выпусков ──────────────────────────────────────────────────────────
HISTORY_DAYS = 7


def load_recent_issues() -> list[dict]:
    issues = []
    for i in range(1, HISTORY_DAYS + 1):
        path = DATA_DIR / f"{(TODAY - timedelta(days=i)).isoformat()}.json"
        if path.exists():
            try:
                issues.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
    return issues


def get_prev_urls(issues: list[dict]) -> set[str]:
    urls: set[str] = set()
    for issue in issues:
        for item in issue.get("news", []):
            for src in item.get("sources", []):
                url = src.get("url", "").split("?")[0].rstrip("/")
                if url:
                    urls.add(url)
            for rel in item.get("related") or []:
                url = rel.get("url", "").split("?")[0].rstrip("/")
                if url:
                    urls.add(url)
    return urls


def get_prev_headlines(issues: list[dict]) -> list[str]:
    return [
        item["headline"]
        for issue in issues
        for item in issue.get("news", [])
        if item.get("headline")
    ]


# ── Индекс ────────────────────────────────────────────────────────────────────
def update_index(date_str: str, count: int) -> None:
    if INDEX_FILE.exists():
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    else:
        index = {"issues": []}
    existing = next((i for i in index["issues"] if i["date"] == date_str), None)
    if existing:
        existing["count"] = count
    else:
        index["issues"].append({"date": date_str, "published": False, "count": count})
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Валидация выпуска ─────────────────────────────────────────────────────────
def _make_id(headline: str, section: str) -> str:
    h = hashlib.md5(headline.encode()).hexdigest()[:6]
    return f"{section}-{h}-{TODAY_STR}"


SENTIMENTS = ("positive", "negative", "neutral")

# Типы событий: код → (русская метка, когда ставить). Из этого словаря собираются
# и валидация, и описание для промпта этапа 4. Метки для сайта — в docs/analytics.
EVENTS: dict[str, tuple[str, str]] = {
    "release":     ("Релиз",          "запуск нового: модель, продукт, функция, открытые веса"),
    "update":      ("Обновление",     "изменение уже существующего: новая версия, расширение доступа, цены, лимиты"),
    "shutdown":    ("Закрытие",       "закрытие или свёртывание продукта, сервиса, компании"),
    "investment":  ("Инвестиции",     "раунды, вложения, госфинансирование, оценка в рамках раунда"),
    "finance":     ("Финансы",        "выручка, расходы, убытки, отчётность, IPO, оценка без раунда"),
    "acquisition": ("Сделка M&A",     "покупка, слияние, поглощение, покупка команды"),
    "partnership": ("Партнёрство",    "партнёрство, контракт, интеграция, поставка вычислений"),
    "regulation":  ("Регулирование",  "законы, требования регуляторов, госполитика, запреты"),
    "legal":       ("Суд",            "иски, суды, приговоры, расследования властей против конкретной компании или человека"),
    "people":      ("Кадры",          "назначения, уходы, найм, увольнения и сокращения"),
    "research":    ("Исследование",   "научные работы, бенчмарки, опросы, отчёты и данные о рынке"),
    "incident":    ("Инцидент",       "сбой, взлом, утечка данных, вред от AI, злоупотребление, мошенничество, скандал"),
    "leak":        ("Утечка",         "утечка сведений о неанонсированном продукте или планах компании"),
    "statement":   ("Заявление",      "заявления, интервью, прогнозы, извинения, мнения публичных лиц"),
    "other":       ("Другое",         "ничего из перечисленного не подходит"),
}

# Что модель иногда пишет вместо кода события
EVENT_SYNONYMS: dict[str, str] = {
    "launch": "release", "new": "release", "announcement": "release",
    "funding": "investment", "round": "investment", "raise": "investment",
    "revenue": "finance", "earnings": "finance", "ipo": "finance", "valuation": "finance",
    "merger": "acquisition", "m&a": "acquisition", "acquire": "acquisition",
    "contract": "partnership", "integration": "partnership", "collaboration": "partnership",
    "law": "regulation", "policy": "regulation", "ban": "regulation",
    "lawsuit": "legal", "court": "legal", "trial": "legal",
    "hiring": "people", "layoffs": "people", "appointment": "people", "departure": "people",
    "study": "research", "paper": "research", "benchmark": "research", "survey": "research", "report": "research",
    "outage": "incident", "breach": "incident", "security": "incident", "scandal": "incident",
    "rumor": "leak",
    "interview": "statement", "opinion": "statement", "forecast": "statement",
}

# Сырые значения event, которых нет ни в EVENTS, ни в синонимах (для лога)
_unknown_events: list[str] = []


def _fix_event(tags: dict) -> None:
    raw = tags.get("event")
    value = str(raw).strip().lower() if raw is not None else ""
    value = EVENT_SYNONYMS.get(value, value)
    if value not in EVENTS:
        if raw:
            _unknown_events.append(str(raw))
        value = "other"
    tags["event"] = value


def events_prompt() -> str:
    return "\n".join(f"  * {code} — {desc}" for code, (_, desc) in EVENTS.items())

# Сколько тональностей по сущностям пришлось дозаполнить общим тоном новости (для лога)
_entity_sentiment_filled = 0


def _fix_entity_tags(item: dict, tags: dict) -> None:
    """
    tags.entities → канонические имена; tags.sentiment → positive|negative|neutral;
    tags.entity_sentiment → {сущность: тональность} ровно по списку entities.

    «rumor» — это не тональность, а достоверность: переводим в unconfirmed=true.
    Принимает и запасной формат entities: [{"name": ..., "sentiment": ...}].
    """
    global _entity_sentiment_filled
    d = entity_directory()

    names: list[str] = []
    pairs: list[tuple[str, object]] = []
    for e in tags.get("entities") or []:
        if isinstance(e, dict) and isinstance(e.get("name"), str):
            names.append(e["name"])
            pairs.append((e["name"], e.get("sentiment")))
        elif isinstance(e, str):
            names.append(e)
    raw_map = tags.get("entity_sentiment")
    if isinstance(raw_map, dict):
        pairs.extend((k, v) for k, v in raw_map.items() if isinstance(k, str))
    tags["entities"] = normalize_entities(names)

    overall = tags.get("sentiment")
    if overall == "rumor":
        item["unconfirmed"] = True
        overall = "neutral"
    if overall not in SENTIMENTS:
        overall = "neutral"
    tags["sentiment"] = overall

    by_entity: dict[str, str] = {}
    for raw_name, value in pairs:
        canon = d.canonical(raw_name)
        if canon == "":
            continue
        canon = canon or " ".join(raw_name.split())
        if value == "rumor":
            value = "neutral"
        if value in SENTIMENTS and canon not in by_entity:
            by_entity[canon] = value

    missing = [e for e in tags["entities"] if e not in by_entity]
    _entity_sentiment_filled += len(missing)
    tags["entity_sentiment"] = {e: by_entity.get(e, overall) for e in tags["entities"]}


def validate_and_fix(item: dict, seen_ids: set) -> dict | None:
    valid_sections     = set(SECTIONS)
    valid_source_types = {"official", "media", "rumor"}

    if not item.get("headline"):
        return None
    if item.get("section") not in valid_sections:
        item["section"] = "industry"

    raw_id = item.get("id") or _make_id(item["headline"], item["section"])
    uid, suffix = raw_id, 2
    while uid in seen_ids:
        uid = f"{raw_id}-{suffix}"
        suffix += 1
    item["id"] = uid
    seen_ids.add(uid)

    TIER_MAP = {"hero": 9, "regular": 7, "compact": 5}
    tier = item.get("tier", "regular")
    if tier not in TIER_MAP:
        tier = "regular"
    item["tier"] = tier
    item["importance"] = TIER_MAP[tier]

    item.setdefault("subheadline", "")
    item.setdefault("body", "")
    item.setdefault("unconfirmed", False)
    item.setdefault("duplicate_note", None)

    fixed_related = []
    for r in item.get("related") or []:
        if not isinstance(r, dict) or not r.get("title") or not r.get("url"):
            continue
        if not str(r["url"]).startswith("http"):
            continue
        fixed_related.append({
            "title":     r["title"],
            "url":       r["url"],
            "entities":  normalize_entities(r.get("entities")),
            "sentiment": r["sentiment"] if r.get("sentiment") in SENTIMENTS else "neutral",
        })
    item["related"] = fixed_related

    item["sources"] = [
        {**s, "type": s["type"] if s.get("type") in valid_source_types else "media"}
        for s in (item.get("sources") or [])
        if isinstance(s, dict) and s.get("url") and str(s["url"]).startswith("http")
    ]

    tags = item.get("tags") or {}
    _fix_entity_tags(item, tags)
    _fix_event(tags)
    item["tags"] = tags

    return item


def finalize_news(news_list: list[dict]) -> list[dict]:
    """Валидация, дедупликация ID, правила tier (ровно один hero)."""
    seen_ids: set[str] = set()
    all_news = [n for item in news_list if (n := validate_and_fix(item, seen_ids))]

    for n in all_news:
        if n.get("unconfirmed") and n["tier"] == "hero":
            log.info("Понижен tier hero→regular (unconfirmed): %s", n["headline"][:60])
            n["tier"] = "regular"
            n["importance"] = 7

    heroes = [n for n in all_news if n["tier"] == "hero"]
    if len(heroes) > 1:
        for n in heroes[1:]:
            log.info("Понижен tier hero→regular (лишний): %s", n["headline"][:60])
            n["tier"] = "regular"
            n["importance"] = 7
    elif not heroes and all_news:
        top = max(all_news, key=lambda n: n["importance"])
        log.info("Повышен tier %s→hero (нет hero): %s", top["tier"], top["headline"][:60])
        top["tier"] = "hero"
        top["importance"] = 9

    global _entity_sentiment_filled
    if _entity_sentiment_filled:
        log.info("Тональность по сущностям: дозаполнено общим тоном новости — %d",
                 _entity_sentiment_filled)
        _entity_sentiment_filled = 0

    if _unknown_events:
        log.info("Неизвестные типы событий → other: %s", ", ".join(sorted(set(_unknown_events))))
        _unknown_events.clear()

    unknown = entity_directory().unknown
    if unknown:
        log.info(
            "Сущности не из entities.yaml (%d) — проверь и добавь в справочник: %s",
            len(unknown), ", ".join(sorted(unknown, key=str.casefold)),
        )
    for err in entity_directory().errors:
        log.warning("entities.yaml: %s", err)

    return all_news


# ── Changelog-карточка ───────────────────────────────────────────────────────
CHANGELOG_FILE = REPO_ROOT / "docs" / "data" / "changelog.json"


def _bump_version(version: str) -> str:
    """v0.01 → v0.02, v0.09 → v0.10 и т.д."""
    import re
    m = re.match(r"^(v\d+\.)(\d+)$", version)
    if not m:
        return version
    return m.group(1) + str(int(m.group(2)) + 1).zfill(len(m.group(2)))


CODE_PATHS = ["scripts/", "docs/app.js", "docs/style.css"]


def _code_changed_since(commit_hash: str) -> bool:
    """Проверяет, менялись ли файлы кода с момента указанного коммита."""
    if not commit_hash:
        return False
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", commit_hash, "HEAD", "--"] + CODE_PATHS,
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def build_changelog_item() -> dict | None:
    """Карточка changelog — добавляется в выпуск только если версия бампнулась.
    Версия бампается если с момента прошлого бампа менялись файлы кода."""
    try:
        cl = json.loads(CHANGELOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        cl = {}

    last_commit = cl.get("_last_version_commit", "")
    if not _code_changed_since(last_commit):
        return None

    cl["version"] = _bump_version(cl.get("version", "v0.01"))
    cl["_last_version_commit"] = _git_head()
    try:
        CHANGELOG_FILE.write_text(json.dumps(cl, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Не удалось сохранить changelog.json: %s", e)

    version = cl.get("version", "v0.01")
    return {
        "id":             f"changelog-{TODAY_STR}",
        "section":        "changelog",
        "tier":           "changelog",
        "version":        version,
        "headline":       f"Нейрогазета {version}",
        "subheadline":    f"Текущая версия: {version}",
        "body":           cl.get("changelog", ""),
        "importance":     0,
        "sources":        [],
        "related":        [],
        "unconfirmed":    False,
        "duplicate_note": None,
        "tags":           {"entities": [], "sentiment": "neutral", "event": "update"},
    }


# ── Вызов claude CLI ──────────────────────────────────────────────────────────
def run_claude(
    prompt: str,
    *,
    model: str = "claude-sonnet-4-6",
    system: str | None = None,
    allowed_tools: list[str] | None = None,
    idle_timeout: int = 180,
    retries: int = 3,
) -> str:
    """
    Вызывает `claude --print --output-format stream-json`, читает события построчно.
    Тайм-аут считается по тишине от CLI (idle_timeout сек без событий) — не по wall-clock,
    чтобы длинные ответы Opus не обрывались.
    """
    cmd = [
        "claude", "--print",
        "--model", model,
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]

    full_prompt = f"<system>\n{system}\n</system>\n\n{prompt}" if system else prompt

    RATE_LIMIT_WAIT = 60 * 60
    RATE_LIMIT_RETRIES = 6

    last_err = ""
    rate_limit_attempt = 0

    for attempt in range(1, retries + 1):
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert proc.stdin and proc.stdout and proc.stderr
        proc.stdin.write(full_prompt)
        proc.stdin.close()

        q: queue.Queue = queue.Queue()

        def _reader(stream, q=q):
            try:
                for line in stream:
                    q.put(line)
            finally:
                q.put(None)

        threading.Thread(target=_reader, args=(proc.stdout,), daemon=True).start()

        result_text = ""
        api_error = ""
        last_activity = time.time()
        hung = False

        while True:
            try:
                line = q.get(timeout=1.0)
            except queue.Empty:
                if time.time() - last_activity > idle_timeout:
                    proc.kill()
                    hung = True
                    break
                continue
            if line is None:
                break
            last_activity = time.time()
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("type") == "result":
                if evt.get("subtype") == "success" and not evt.get("is_error"):
                    result_text = evt.get("result", "") or result_text
                else:
                    api_error = evt.get("result") or evt.get("api_error_status") or "result error"

        proc.wait()
        stderr_out = (proc.stderr.read() or "").strip()

        if hung:
            last_err = f"idle timeout ({idle_timeout}s без событий)"
            log.warning("claude CLI зависание (попытка %d/%d), kill()", attempt, retries)
        elif proc.returncode == 0 and result_text and not api_error:
            return result_text.strip()
        else:
            last_err = api_error or stderr_out or f"exit={proc.returncode}"
            if "rate limit" in last_err.lower():
                rate_limit_attempt += 1
                if rate_limit_attempt > RATE_LIMIT_RETRIES:
                    raise RuntimeError(
                        f"claude CLI: rate limit не снялся за {RATE_LIMIT_RETRIES} ч. Последняя ошибка: {last_err}"
                    )
                log.warning(
                    "claude CLI: rate limit (%d/%d), жду %d мин…",
                    rate_limit_attempt, RATE_LIMIT_RETRIES, RATE_LIMIT_WAIT // 60,
                )
                time.sleep(RATE_LIMIT_WAIT)
                continue
            log.warning("claude CLI попытка %d/%d: %s", attempt, retries, last_err[:200])

        if attempt < retries:
            time.sleep(15)

    raise RuntimeError(f"claude CLI: все {retries} попытки исчерпаны. Последняя ошибка: {last_err}")
