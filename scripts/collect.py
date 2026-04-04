#!/usr/bin/env python3
"""
Нейрогазета — скрипт сборки выпуска.

Один запрос к Anthropic API с веб-поиском (max_uses=3),
собирает AI-новости за последние 24 часа, сохраняет выпуск в docs/data/.
"""

import asyncio
import json
import os
import sys
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic
from anthropic import AsyncAnthropic
import logging

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Конфигурация ────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "docs" / "data"
INDEX_FILE = DATA_DIR / "index.json"
LATEST_FILE = DATA_DIR / "latest.json"

MODEL = "claude-sonnet-4-6"

NOW = datetime.now(timezone(timedelta(hours=3)))            # UTC+3 (МСК)
TODAY = NOW.date()
TODAY_STR = TODAY.isoformat()
YESTERDAY_STR = (TODAY - timedelta(days=1)).isoformat()

# Точный диапазон сбора: последние 24 часа
PERIOD_FROM = (NOW - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M МСК")
PERIOD_TO   = NOW.strftime("%Y-%m-%d %H:%M МСК")

SECTIONS = ["models", "platforms", "industry", "hype"]
SECTION_NAMES = {
    "models":    "Модели (выпуски и обновления языковых и мультимодальных моделей)",
    "platforms": "Платформы (инструменты, IDE, API, продукты на базе AI)",
    "industry":  "Индустрия (инвестиции, регуляция, кадры, бизнес)",
    "hype":      "Желтуха (слухи, утечки, неподтверждённые данные, курьёзы)",
}

# Направляющие поисковые запросы по каждой рубрике
SECTION_QUERIES = {
    "models": [
        f"new AI language model release {TODAY_STR} OR {YESTERDAY_STR}",
        f"LLM benchmark results {TODAY_STR} OR {YESTERDAY_STR}",
        f"OpenAI Anthropic Google DeepMind model announcement {TODAY_STR} OR {YESTERDAY_STR}",
        f"multimodal AI model update {TODAY_STR} OR {YESTERDAY_STR}",
    ],
    "platforms": [
        f"AI developer tools release {TODAY_STR} OR {YESTERDAY_STR}",
        f"AI API update IDE integration {TODAY_STR} OR {YESTERDAY_STR}",
        f"new AI product launch {TODAY_STR} OR {YESTERDAY_STR}",
        f"AI coding assistant update {TODAY_STR} OR {YESTERDAY_STR}",
    ],
    "industry": [
        f"AI startup funding investment {TODAY_STR} OR {YESTERDAY_STR}",
        f"AI regulation policy {TODAY_STR} OR {YESTERDAY_STR}",
        f"AI company acquisition merger {TODAY_STR} OR {YESTERDAY_STR}",
        f"AI executive hire {TODAY_STR} OR {YESTERDAY_STR}",
    ],
    "hype": [
        f"AI leak rumor unconfirmed {TODAY_STR} OR {YESTERDAY_STR}",
        f"AI controversy scandal {TODAY_STR} OR {YESTERDAY_STR}",
        f"AI model capability claim {TODAY_STR} OR {YESTERDAY_STR}",
    ],
}

# JSON-схема вынесена в системный промпт — кешируется между retry-попытками
SYSTEM_PROMPT = """Ты редактор профессионального ежедневного издания об AI «Нейрогазета».
Твой голос: факты и конкретика, без метафор, без восхищения, без воды.
Правила:
- Включай ТОЛЬКО новости, опубликованные в указанный период сбора. Игнорируй более старые материалы, даже если они релевантны.
- Пиши только то, что подтверждено источниками или явно помечай как слух.
- Не раздувай выпуск: если новостей мало — пиши мало.
- Дедупликация: если одна новость в нескольких источниках — одна запись с полем duplicate_note.
- Язык: русский, деловой стиль.
- importance: 9-10 главная новость дня, 6-8 важная, 1-5 краткая заметка.
- Количество новостей на рубрику: целевой диапазон 5-7, но только реальные.
  Если реальных новостей 2 — пиши 2, не заполняй рубрику мусором.
  Если новостей больше 7 — отбери 7 самых важных по importance, остальные отсеки.
  Обязательные условия для включения: есть конкретный источник с URL, новость произошла в период сбора.
  Принцип: лучше меньше честных новостей, чем много выдуманных.
- Возвращай ТОЛЬКО валидный JSON, без markdown-блоков и комментариев.

Формат ответа — один JSON-объект со всеми рубриками:
{
  "news": [
    {
      "id": "уникальный-id-через-дефис",
      "section": "models|platforms|industry|hype",
      "headline": "Заголовок на русском",
      "subheadline": "Одно предложение — суть новости",
      "body": "Полный текст. Факты, цифры, прямые цитаты.",
      "importance": 1,
      "sources": [
        {"title": "Название источника", "url": "https://...", "type": "official|media|rumor"}
      ],
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


def make_user_prompt(retry_hint: str = "") -> str:
    hint = f"\n\nВАЖНО: {retry_hint}" if retry_hint else ""
    sections_block = ""
    for key in SECTIONS:
        queries = "\n".join(f'  - "{q}"' for q in SECTION_QUERIES[key])
        sections_block += f"\n### {SECTION_NAMES[key]}\n{queries}\n"

    return f"""Дата выпуска: {TODAY_STR}
Период сбора: с {PERIOD_FROM} по {PERIOD_TO} (строго последние 24 часа){hint}

Выполни поиск и собери новости по всем четырём рубрикам.
Включай только материалы, опубликованные в период сбора.
{sections_block}
Приоритет источников:
- Официальные блоги: openai.com/blog, anthropic.com/news, deepmind.google/blog, ai.meta.com/blog, mistral.ai/news, x.ai/blog, stability.ai/news
- Отраслевые СМИ: techcrunch.com, theverge.com, reuters.com, bloomberg.com, wsj.com, wired.com
- Открытый веб"""


# ── Вспомогательные функции ─────────────────────────────────────────────────

def load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {"issues": []}


def save_index(index: dict) -> None:
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def update_index(date_str: str, count: int, published: bool) -> None:
    index = load_index()
    existing = next((i for i in index["issues"] if i["date"] == date_str), None)
    if existing:
        existing["count"] = count
        existing["published"] = published
    else:
        index["issues"].append({"date": date_str, "published": published, "count": count})
    save_index(index)


def make_id(headline: str, section: str, date: str) -> str:
    slug = headline.lower()[:40].replace(" ", "-")
    slug = "".join(c if c.isalnum() or c == "-" else "" for c in slug)
    h = hashlib.md5(headline.encode()).hexdigest()[:6]
    return f"{section}-{h}-{date}"


def extract_json(text: str) -> str:
    """Убирает markdown-обёртки и возвращает чистый JSON."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return text.strip()


def validate_and_fix(item: dict, section: str, seen_ids: set) -> dict | None:
    """Валидирует одну новость; возвращает None если невалидна."""
    valid_sections = set(SECTIONS)
    valid_sentiments = {"positive", "negative", "neutral", "rumor"}
    valid_events = {"release", "update", "shutdown", "investment", "regulation", "leak"}
    valid_source_types = {"official", "media", "rumor"}

    if not item.get("headline"):
        return None

    if item.get("section") not in valid_sections:
        item["section"] = section

    raw_id = item.get("id") or make_id(item["headline"], item["section"], TODAY_STR)
    uid = raw_id
    suffix = 2
    while uid in seen_ids:
        uid = f"{raw_id}-{suffix}"
        suffix += 1
    item["id"] = uid
    seen_ids.add(uid)

    try:
        imp = int(item.get("importance", 5))
        item["importance"] = max(1, min(10, imp))
    except (TypeError, ValueError):
        item["importance"] = 5

    item.setdefault("subheadline", "")
    item.setdefault("body", "")
    item.setdefault("unconfirmed", False)
    item.setdefault("duplicate_note", None)

    sources = item.get("sources", []) or []
    item["sources"] = [
        {**s, "type": s.get("type") if s.get("type") in valid_source_types else "media"}
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


# ── Единый запрос с retry ────────────────────────────────────────────────────

def _retry_after(e: anthropic.RateLimitError, fallback: float = 65.0) -> float:
    """Читает retry-after из заголовков ответа, иначе возвращает fallback."""
    try:
        val = e.response.headers.get("retry-after")
        if val:
            return float(val) + 2  # +2с буфер
    except Exception:
        pass
    return fallback


async def _request_all(client: AsyncAnthropic, retry_hint: str = "") -> list[dict]:
    """Один запрос к API для всех рубрик. Возвращает плоский список новостей."""
    response = await client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
        messages=[{"role": "user", "content": make_user_prompt(retry_hint)}],
    )

    text = "".join(block.text for block in response.content if hasattr(block, "text"))
    text = extract_json(text)
    data = json.loads(text)  # бросит JSONDecodeError → уйдёт в retry
    return data.get("news", [])


async def collect_all(api_key: str) -> dict:
    """Сбор всех рубрик за один запрос: при 429 ждёт retry-after, до 5 попыток."""
    async with AsyncAnthropic(api_key=api_key) as client:
        log.info("Сбор всех рубрик одним запросом через %s…", MODEL)
        last_error = ""
        news_list = []
        for attempt in range(1, 6):
            try:
                hint = f"Предыдущий ответ не был валидным JSON ({last_error}). Верни ТОЛЬКО JSON, без пояснений." if last_error else ""
                news_list = await _request_all(client, hint)
                log.info("Получено %d новостей (попытка %d)", len(news_list), attempt)
                break
            except json.JSONDecodeError as e:
                last_error = str(e)
                log.warning("Невалидный JSON, попытка %d/5: %s", attempt, e)
                if attempt < 5:
                    await asyncio.sleep(10)
            except anthropic.RateLimitError as e:
                wait = _retry_after(e)
                log.warning("Rate limit, жду %.0fс (попытка %d/5)…", wait, attempt)
                if attempt < 5:
                    await asyncio.sleep(wait)
            except anthropic.APIError as e:
                last_error = str(e)
                log.warning("Ошибка API, попытка %d/5: %s", attempt, e)
                if attempt < 5:
                    await asyncio.sleep(15)
        else:
            log.error("Все попытки исчерпаны, выпуск пустой")

    seen_ids: set[str] = set()
    all_news = []
    for item in news_list:
        section = item.get("section") if item.get("section") in SECTIONS else "models"
        fixed = validate_and_fix(item, section, seen_ids)
        if fixed:
            all_news.append(fixed)

    return {
        "date": TODAY_STR,
        "published": False,
        "news": all_news,
    }


# ── Основная логика ──────────────────────────────────────────────────────────

def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY не задан")
        sys.exit(1)

    log.info("Дата выпуска: %s", TODAY_STR)

    try:
        issue = asyncio.run(collect_all(api_key))
    except Exception as e:
        log.error("Критическая ошибка: %s", e)
        sys.exit(1)

    count = len(issue.get("news", []))
    log.info("Итого новостей: %d", count)

    out_path = DATA_DIR / f"{TODAY_STR}.json"
    out_path.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Сохранено: %s", out_path)

    LATEST_FILE.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Обновлён: %s", LATEST_FILE)

    update_index(TODAY_STR, count, published=False)
    log.info("Индекс обновлён")


if __name__ == "__main__":
    main()
