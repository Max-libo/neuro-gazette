#!/usr/bin/env python3
"""
Нейрогазета — скрипт сборки выпуска.

Этап 1: RSS и scrape из sources.yaml (тип rss / scrape).
Этап 2: Веб-поиск Claude для рубрики hype (запросы из sources.yaml, max_uses=2).
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
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
import anthropic
from anthropic import AsyncAnthropic

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Конфигурация ─────────────────────────────────────────────────────────────

REPO_ROOT    = Path(__file__).parent.parent
DATA_DIR     = REPO_ROOT / "docs" / "data"
INDEX_FILE   = DATA_DIR / "index.json"
LATEST_FILE  = DATA_DIR / "latest.json"
SOURCES_FILE = REPO_ROOT / "sources.yaml"

MODEL = "claude-sonnet-4-6"

NOW        = datetime.now(timezone(timedelta(hours=3)))   # UTC+3 (МСК)
TODAY      = NOW.date()
TODAY_STR  = TODAY.isoformat()
CUTOFF_24H = (NOW - timedelta(hours=24)).astimezone(timezone.utc)
CUTOFF_48H = (NOW - timedelta(hours=48)).astimezone(timezone.utc)

SECTIONS     = ["models", "platforms", "industry", "hype"]
RAW_LIMIT    = 60   # максимум статей на вход Claude
MIN_ARTICLES = 10   # если меньше — расширяем окно до 48ч

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NeuroGazeta/1.0)",
    "Accept": "text/html,application/rss+xml,application/xml,*/*",
}

# ── Загрузка источников ───────────────────────────────────────────────────────

def load_sources() -> dict:
    """Читает sources.yaml из корня репозитория."""
    with open(SOURCES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)

# ── Вспомогательные функции ───────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_date(value: str) -> datetime | None:
    """Пробует распарсить строку даты в datetime с tzinfo."""
    if not value:
        return None
    value = value.strip()
    # Обрезаем лишние части (миллисекунды и т.п.)
    value = re.sub(r"\.\d+", "", value)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
    ):
        try:
            dt = datetime.strptime(value[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return None


# ── RSS ───────────────────────────────────────────────────────────────────────

def fetch_rss_feed(source: dict, cutoff: datetime) -> list[dict]:
    url  = source["url"]
    name = source["name"]
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("RSS %s — ошибка: %s", name, e)
        return []

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

        if pub_dt and pub_dt < cutoff:
            continue

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
            "title":     title,
            "url":       link,
            "source":    name,
            "published": pub_str,
            "summary":   summary,
        })

    log.debug("RSS %s: %d статей", name, len(result))
    return result


# ── Scrape ────────────────────────────────────────────────────────────────────

def scrape_page(source: dict, cutoff: datetime) -> list[dict]:
    """
    Парсит HTML-страницу в поисках заголовков и ссылок на статьи.
    Сначала пробует JSON-LD, затем fallback на теги h1/h2/h3.
    """
    url  = source["url"]
    name = source["name"]
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.warning("Scrape %s — ошибка: %s", name, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict] = []
    seen_urls: set[str] = set()

    # 1. JSON-LD structured data (самый точный метод)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("@type") not in ("Article", "NewsArticle", "BlogPosting"):
                    continue
                title = (item.get("headline") or "").strip()
                main  = item.get("mainEntityOfPage")
                link  = (
                    item.get("url") or
                    (main.get("@id") if isinstance(main, dict) else None) or
                    ""
                ).strip()
                if not link.startswith("http"):
                    link = urljoin(url, link)
                if not title or not link or link in seen_urls:
                    continue
                pub_dt = _parse_date(
                    item.get("datePublished") or item.get("dateModified") or ""
                )
                if pub_dt and pub_dt < cutoff:
                    continue
                seen_urls.add(link)
                results.append({
                    "title":     title,
                    "url":       link,
                    "source":    name,
                    "published": pub_dt.date().isoformat() if pub_dt else TODAY_STR,
                    "summary":   (item.get("description") or "")[:500],
                })
        except Exception:
            pass

    if results:
        log.debug("Scrape %s (JSON-LD): %d статей", name, len(results))
        return results

    # 2. Fallback: поиск заголовков h1/h2/h3 со ссылками
    for heading in soup.find_all(["h1", "h2", "h3"]):
        # Ищем ссылку внутри заголовка или в ближайшем родителе
        a = heading.find("a", href=True)
        if not a:
            parent = heading.parent
            a = parent.find("a", href=True) if parent else None
        if not a:
            continue

        title = heading.get_text(strip=True)
        if len(title) < 15:
            continue

        link = a["href"]
        if not link.startswith("http"):
            link = urljoin(url, link)
        if link in seen_urls or link.rstrip("/") == url.rstrip("/"):
            continue
        seen_urls.add(link)

        # Ищем дату в ближайшем блоке-предке
        pub_dt = None
        block  = heading.find_parent(["article", "div", "li", "section"])
        if block:
            time_tag = block.find("time")
            if time_tag:
                pub_dt = _parse_date(
                    time_tag.get("datetime", "") or time_tag.get_text()
                )
            if not pub_dt:
                for elem in block.find_all(["span", "p", "div"], limit=15):
                    cls = " ".join(elem.get("class") or [])
                    if any(k in cls.lower() for k in ("date", "time", "publish", "meta", "ago")):
                        pub_dt = _parse_date(elem.get_text(strip=True))
                        if pub_dt:
                            break

        if pub_dt and pub_dt < cutoff:
            continue

        results.append({
            "title":     title,
            "url":       link,
            "source":    name,
            "published": pub_dt.date().isoformat() if pub_dt else TODAY_STR,
            "summary":   "",
        })

    log.debug("Scrape %s (HTML): %d статей", name, len(results))
    return results


# ── Сбор всех источников ──────────────────────────────────────────────────────

def collect_from_sources(sources: list[dict], cutoff: datetime) -> list[dict]:
    """Параллельно собирает материалы из RSS и scrape-источников."""
    rss_sources    = [s for s in sources if s.get("type") == "rss"]
    scrape_sources = [s for s in sources if s.get("type") == "scrape"]

    all_items: list[dict] = []

    if rss_sources:
        with ThreadPoolExecutor(max_workers=len(rss_sources)) as ex:
            for items in ex.map(lambda s: fetch_rss_feed(s, cutoff), rss_sources):
                all_items.extend(items)

    if scrape_sources:
        with ThreadPoolExecutor(max_workers=min(10, len(scrape_sources))) as ex:
            for items in ex.map(lambda s: scrape_page(s, cutoff), scrape_sources):
                all_items.extend(items)

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


# ── Hype через веб-поиск ──────────────────────────────────────────────────────

async def fetch_hype_via_search(client: AsyncAnthropic, queries: list[str]) -> str:
    """Запросы к Claude с web_search для hype-материалов."""
    blocks: list[str] = []

    for query in queries:
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


def build_user_prompt(articles: list[dict], hype_text: str) -> str:
    lines = [f"Дата выпуска: {TODAY_STR}\n"]

    lines.append("=== МАТЕРИАЛЫ (рубрики models / platforms / industry) ===")
    lines.append(f"Статей: {len(articles)}\n")
    for i, a in enumerate(articles, 1):
        lines.append(
            f"[{i}] {a['title']}\n"
            f"    Источник: {a['source']} | Дата: {a['published']}\n"
            f"    URL: {a['url']}\n"
            f"    Аннотация: {a.get('summary') or '—'}\n"
        )

    if hype_text:
        lines.append("\n=== ВЕБ-ПОИСК (рубрика hype) ===")
        lines.append(hype_text)

    lines.append("\nОтсортируй, дедуплицируй и оформи выпуск по схеме.")
    return "\n".join(lines)


# ── Claude API ────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return text.strip()


async def process_with_claude(
    client: AsyncAnthropic,
    articles: list[dict],
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
                    "content": build_user_prompt(articles, hype_text) + hint,
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

    # ── Загружаем sources.yaml ─────────────────────────────────────────────────
    config  = load_sources()
    sources = config.get("sources", [])
    hype_queries = (config.get("search_queries") or {}).get("hype", [])
    log.info("Источников: %d, hype-запросов: %d", len(sources), len(hype_queries))

    client = AsyncAnthropic(api_key=anthropic_key)

    # ── Этап 1: сбор материалов за 24ч ───────────────────────────────────────
    log.info("Сбор материалов (окно 24ч)…")
    raw_items = collect_from_sources(sources, CUTOFF_24H)
    raw_items = deduplicate_raw(raw_items)
    log.info("Собрано (24ч): %d статей", len(raw_items))

    # Если мало — расширяем до 48ч
    if len(raw_items) < MIN_ARTICLES:
        log.info("Меньше %d статей — расширяем окно до 48ч…", MIN_ARTICLES)
        raw_items = collect_from_sources(sources, CUTOFF_48H)
        raw_items = deduplicate_raw(raw_items)
        log.info("Собрано (48ч): %d статей", len(raw_items))

    raw_items = raw_items[:RAW_LIMIT]

    # ── Этап 2: hype через веб-поиск ─────────────────────────────────────────
    log.info("Сбор hype через веб-поиск…")
    hype_text = await fetch_hype_via_search(client, hype_queries)

    if not raw_items and not hype_text:
        log.error("Нет материалов — выпуск пустой")
        sys.exit(1)

    # ── Этап 3: Claude API ────────────────────────────────────────────────────
    news_list = await process_with_claude(client, raw_items, hype_text)

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
