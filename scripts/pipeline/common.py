"""Общие константы, пути и утилиты для пайплайна Нейрогазеты."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

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


def validate_and_fix(item: dict, seen_ids: set) -> dict | None:
    valid_sections     = set(SECTIONS)
    valid_sentiments   = {"positive", "negative", "neutral", "rumor"}
    valid_events       = {"release", "update", "shutdown", "investment", "regulation", "leak"}
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
            "entities":  r.get("entities") or [],
            "sentiment": r["sentiment"] if r.get("sentiment") in {"positive", "negative", "neutral"} else "neutral",
        })
    item["related"] = fixed_related

    item["sources"] = [
        {**s, "type": s["type"] if s.get("type") in valid_source_types else "media"}
        for s in (item.get("sources") or [])
        if isinstance(s, dict) and s.get("url") and str(s["url"]).startswith("http")
    ]

    tags = item.get("tags") or {}
    tags.setdefault("entities", [])
    if tags.get("sentiment") not in valid_sentiments:
        tags["sentiment"] = "neutral"
    if tags.get("event") not in valid_events:
        tags["event"] = "update"
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

    return all_news


# ── Вызов claude CLI ──────────────────────────────────────────────────────────
def run_claude(
    prompt: str,
    *,
    model: str = "claude-sonnet-4-6",
    system: str | None = None,
    allowed_tools: list[str] | None = None,
    timeout: int = 600,
    retries: int = 3,
) -> str:
    """
    Вызывает `claude --print` с промптом через stdin.
    Использует токены подписки (не API-ключ).
    """
    cmd = ["claude", "--print", "--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]

    # Системный промпт добавляем в начало сообщения, если CLI не поддерживает --system-prompt
    full_prompt = f"<system>\n{system}\n</system>\n\n{prompt}" if system else prompt

    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            last_err = result.stderr.strip()
            log.warning("claude CLI попытка %d/%d: %s", attempt, retries, last_err[:200])
        except subprocess.TimeoutExpired:
            last_err = f"timeout ({timeout}s)"
            log.warning("claude CLI таймаут, попытка %d/%d", attempt, retries)
        if attempt < retries:
            time.sleep(15)

    raise RuntimeError(f"claude CLI: все {retries} попытки исчерпаны. Последняя ошибка: {last_err}")
