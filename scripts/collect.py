#!/usr/bin/env python3
"""
Нейрогазета — скрипт сборки выпуска.

Этап 1: RSS-ленты (модели / платформы / индустрия) через feedparser.
Этап 2: Веб-поиск Claude только для рубрики hype (2 запроса, max_uses=2).
Этап 3: Один вызов Claude API — сортировка, дедупликация, JSON.
"""

import asyncio
import json
import os
import re
import sys
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests
import anthropic
from anthropic import AsyncAnthropic

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Конфигурация ─────────────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).parent.parent
DATA_DIR    = REPO_ROOT / "docs" / "data"
INDEX_FILE  = DATA_DIR / "index.json"
LATEST_FILE = DATA_DIR / "latest.json"

MODEL = "claude-sonnet-4-6"

NOW        = datetime.now(timezone(timedelta(hours=3)))   # UTC+3 (МСК)
TODAY      = NOW.date()
TODAY_STR  = TODAY.isoformat()
CUTOFF_UTC = (NOW - timedelta(hours=24)).astimezone(timezone.utc)

SECTIONS  = ["models", "platforms", "industry", "hype"]
RAW_LIMIT = 60   # максимум RSS-статей на вход Claude

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NeuroGazeta/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# ── Кандидаты RSS-лент ────────────────────────────────────────────────────────

RSS_FEEDS_CANDIDATE = [
    # Официальные блоги моделей
    "https://openai.com/blog/rss.xml",
    "https://www.anthropic.com/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://ai.meta.com/blog/feed/",
    "https://mistral.ai/news/rss",
    "https://blog.xai.com/rss",
    "https://huggingface.co/blog/feed.xml",
    "https://cohere.com/blog/rss",
    "https://stability.ai/news/rss",
    "https://runwayml.com/blog/rss",
    "https://elevenlabs.io/blog/rss",
    "https://www.midjourney.com/updates/rss",
    "https://developers.sber.ru/blog/rss",
    "https://yandex.ru/blog/rss",
    # Медиа об AI
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.wired.com/feed/tag/ai/latest/rss",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed",
]

# ── Проверка живых лент ───────────────────────────────────────────────────────

def _check_feed(url: str) -> str | None:
    """Проверяет доступность RSS-ленты. Возвращает URL если лента рабочая."""
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if resp.status_code != 200:
            log.debug("Feed dead (%d): %s", resp.status_code, url)
            return None
        parsed = feedparser.parse(resp.content)
        if not parsed.entries:
            log.debug("Feed empty: %s", url)
            return None
        return url
    except Exception as e:
        log.debug("Feed error (%s): %s", url, e)
        return None


def probe_feeds(candidates: list[str]) -> list[str]:
    """Параллельно проверяет ленты, возвращает только рабочие."""
    with ThreadPoolExecutor(max_workers=len(candidates)) as ex:
        results = list(ex.map(_check_feed, candidates))
    live = [url for url in results if url]
    log.info("Живых лент: %d из %d", len(live), len(candidates))
    return live


# ── Этап 1: RSS ───────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_rss_feed(url: str) -> list[dict]:
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("RSS %s — ошибка: %s", url, e)
        return []

    domain = url.split("/")[2]
    result = []
    for entry in feed.entries:
        pub_dt = None
        for attr in ("published_parsed", "updated_parsed"):
            val = getattr(entry, attr, None)
            if val:
                try:
                    pub_dt = datetime(*val[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
                break

        if pub_dt and pub_dt < CUTOFF_UTC:
            continue  # старше 24ч — пропускаем

        title = (getattr(entry, "title", "") or "").strip()
        if not title:
            continue

        link    = getattr(entry, "link", "") or ""
        summary = _clean_text(
            getattr(entry, "summary", "") or
            getattr(entry, "description", "") or ""
        )[:500]
        pub_str = pub_dt.date().isoformat() if pub_dt else TODAY_STR

        result.append({
            "title": title,
            "url": link,
            "source": domain,
            "published": pub_str,
            "summary": summary,
        })

    return result


def fetch_all_rss(live_feeds: list[str]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=max(1, len(live_feeds))) as ex:
        results = list(ex.map(fetch_rss_feed, live_feeds))
    all_items = [item for chunk in results for item in chunk]
    log.info("RSS: %d статей из %d живых лент", len(all_items), len(live_feeds))
    return all_items


def deduplicate_raw(articles: list[dict]) -> list[dict]:
    seen_urls:   set[str] = set()
    seen_titles: set[str] = set()
    result = []
    for a in articles:
        url       = a.get("url", "").split("?")[0].rstrip("/")
        title_key = re.sub(r"\W+", " ", a.get("title", "").lower())[:60].strip()
        if (url and url in seen_urls) or title_key in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        if title_key:
            seen_titles.add(title_key)
        result.append(a)
    return result


# ── Этап 2: веб-поиск для hype ────────────────────────────────────────────────

HYPE_QUERIES = [
    "AI news viral controversy today",
    "нейросети скандал обсуждение сегодня",
]


async def fetch_hype_via_search(client: AsyncAnthropic) -> str:
    """Два запроса к Claude с web_search для hype-материалов.
    Возвращает сводный текст или пустую строку."""
    blocks: list[str] = []

    for query in HYPE_QUERIES:
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=1000,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Найди самые обсуждаемые и скандальные AI-новости по запросу: «{query}». "
                        "Перечисли 3-5 результатов с заголовком, URL и кратким описанием. "
                        "Только факты, без оценок."
                    ),
                }],
            )
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    blocks.append(block.text.strip())
        except Exception as e:
            log.warning("Hype search (%r) ошибка: %s", query, e)

    result = "\n\n---\n\n".join(blocks)
    log.info("Hype web-search: %d символов", len(result))
    return result


# ── SYSTEM_PROMPT ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты редактор профессионального ежедневного издания об AI «Нейрогазета».
Голос: факты и конкретика, без метафор, без восхищения, без воды.

Правила:
- Отбирай только новости об AI/ML: модели, инструменты, инвестиции, регуляция, утечки.
- Игнорируй нерелевантное: спорт, общая политика, новости без связи с AI.
- Дедупликация: несколько источников об одном событии — одна запись, поле duplicate_note.
- Целевой диапазон: 5-7 новостей на рубрику. Меньше честных лучше, чем больше выдуманных.
- Если новостей по рубрике больше 7 — оставь топ-7 по importance, остальные отсеки.
- importance: 9-10 главная новость дня, 6-8 важная, 1-5 краткая заметка.
- Язык выпуска: русский, деловой стиль. Переводи заголовки и тексты на русский.
- Возвращай ТОЛЬКО валидный JSON без markdown-блоков и комментариев.

Рубрики:
- models    — выпуски и обновления языковых и мультимодальных моделей
- platforms — инструменты, IDE, API, продукты на базе AI
- industry  — инвестиции, регуляция, кадры, бизнес
- hype      — слухи, утечки, неподтверждённые данные, курьёзы

Формат ответа:
{
  "news": [
    {
      "id": "section-uniqueid",
      "section": "models|platforms|industry|hype",
      "headline": "Заголовок на русском",
      "subheadline": "Одно предложение — суть новости",
      "body": "Полный текст. Факты, цифры, прямые цитаты.",
      "importance": 1,
      "sources": [{"title": "Название", "url": "https://...", "type": "official|media|rumor"}],
      "unconfirmed": false,
      "duplicate_note": null,
      "tags": {
        "entities": ["название-сущности"],
        "sentiment": "positive|negative|neutral|rumor",
        "event": "release|update|shutdown|investment|regulation|leak"
      }
    }
  ]
}"""


def build_user_prompt(rss_articles: list[dict], hype_text: str) -> str:
    lines = [f"Дата выпуска: {TODAY_STR}\n"]

    lines.append("=== RSS-МАТЕРИАЛЫ (рубрики models / platforms / industry) ===")
    lines.append(f"Статей: {len(rss_articles)}\n")
    for i, a in enumerate(rss_articles, 1):
        lines.append(
            f"[{i}] {a['title']}\n"
            f"    Источник: {a['source']} | Дата: {a['published']}\n"
            f"    URL: {a['url']}\n"
            f"    Аннотация: {a['summary'] or '—'}\n"
        )

    if hype_text:
        lines.append("\n=== ВЕБ-ПОИСК (рубрика hype) ===")
        lines.append(hype_text)

    lines.append("\nОтсортируй, дедуплицируй и оформи выпуск по схеме.")
    return "\n".join(lines)


# ── Этап 3: Claude API ────────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return text.strip()


async def process_with_claude(
    client: AsyncAnthropic,
    rss_articles: list[dict],
    hype_text: str,
) -> list[dict]:
    """Один запрос к Claude (без инструментов): обработка материалов в JSON."""
    log.info("Пауза 15с перед вызовом Claude API…")
    await asyncio.sleep(15)

    last_err = ""
    for attempt in range(1, 4):
        try:
            hint = (
                f"\n\nВАЖНО: предыдущий ответ не был валидным JSON ({last_err}). "
                "Верни ТОЛЬКО JSON, без пояснений."
            ) if last_err else ""

            response = await client.messages.create(
                model=MODEL,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": build_user_prompt(rss_articles, hype_text) + hint,
                }],
            )
            text = _extract_json(response.content[0].text)
            data = json.loads(text)
            news = data.get("news", [])
            log.info("Claude: оформлено %d новостей (попытка %d)", len(news), attempt)
            return news

        except json.JSONDecodeError as e:
            last_err = str(e)
            log.warning("Невалидный JSON, попытка %d/3: %s", attempt, e)
            if attempt < 3:
                await asyncio.sleep(10)

        except anthropic.RateLimitError as e:
            wait = 65.0
            try:
                val = e.response.headers.get("retry-after")
                if val:
                    wait = float(val) + 2
            except Exception:
                pass
            log.warning("Rate limit, жду %.0fс (попытка %d/3)…", wait, attempt)
            if attempt < 3:
                await asyncio.sleep(wait)

        except anthropic.APIError as e:
            last_err = str(e)
            log.warning("Ошибка API, попытка %d/3: %s", attempt, e)
            if attempt < 3:
                await asyncio.sleep(15)

    log.error("Claude: все попытки исчерпаны")
    return []


# ── Валидация ─────────────────────────────────────────────────────────────────

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

    try:
        item["importance"] = max(1, min(10, int(item.get("importance", 5))))
    except (TypeError, ValueError):
        item["importance"] = 5

    item.setdefault("subheadline", "")
    item.setdefault("body", "")
    item.setdefault("unconfirmed", False)
    item.setdefault("duplicate_note", None)

    sources = item.get("sources") or []
    item["sources"] = [
        {**s, "type": s["type"] if s.get("type") in valid_source_types else "media"}
        for s in sources if isinstance(s, dict) and s.get("url")
    ]

    tags = item.get("tags") or {}
    tags.setdefault("entities", [])
    if tags.get("sentiment") not in valid_sentiments:
        tags["sentiment"] = "neutral"
    if tags.get("event") not in valid_events:
        tags["event"] = "update"
    item["tags"] = tags

    return item


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


# ── Точка входа ───────────────────────────────────────────────────────────────

async def amain() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        log.error("ANTHROPIC_API_KEY не задан")
        sys.exit(1)

    client = AsyncAnthropic(api_key=anthropic_key)

    # ── Этап 1: RSS ────────────────────────────────────────────────────────────
    log.info("Проверяем RSS-ленты…")
    live_feeds = probe_feeds(RSS_FEEDS_CANDIDATE)

    rss_raw = fetch_all_rss(live_feeds)
    rss_raw = deduplicate_raw(rss_raw)[:RAW_LIMIT]
    log.info("RSS после дедупликации: %d статей", len(rss_raw))

    # ── Этап 2: веб-поиск для hype ────────────────────────────────────────────
    log.info("Сбор hype через веб-поиск…")
    hype_text = await fetch_hype_via_search(client)

    if not rss_raw and not hype_text:
        log.error("Нет материалов — выпуск пустой")
        sys.exit(1)

    # ── Этап 3: Claude API ─────────────────────────────────────────────────────
    news_list = await process_with_claude(client, rss_raw, hype_text)

    seen_ids: set[str] = set()
    all_news = [n for item in news_list if (n := validate_and_fix(item, seen_ids))]
    log.info("Итого новостей в выпуске: %d", len(all_news))

    issue    = {"date": TODAY_STR, "published": False, "news": all_news}
    out_path = DATA_DIR / f"{TODAY_STR}.json"
    out_path.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_FILE.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    update_index(TODAY_STR, len(all_news))
    log.info("Готово: %s", out_path)

    await client.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
