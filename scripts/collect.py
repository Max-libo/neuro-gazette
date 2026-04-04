#!/usr/bin/env python3
"""
Нейрогазета — скрипт сборки выпуска.

Вызывает Anthropic API с веб-поиском, собирает AI-новости за последние 24 часа,
сохраняет выпуск в docs/data/YYYY-MM-DD.json и обновляет docs/data/latest.json.
"""

import json
import os
import sys
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic

# ── Конфигурация ────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "docs" / "data"
INDEX_FILE = DATA_DIR / "index.json"
LATEST_FILE = DATA_DIR / "latest.json"

MODEL = "claude-opus-4-6"          # самая мощная модель для сборки

TODAY = datetime.now(timezone(timedelta(hours=3))).date()   # UTC+3 (МСК)
TODAY_STR = TODAY.isoformat()

SECTIONS = ["models", "platforms", "industry", "hype"]
SECTION_NAMES = {
    "models": "Модели (выпуски и обновления языковых моделей)",
    "platforms": "Платформы (инструменты, IDE, API, продукты на базе AI)",
    "industry": "Индустрия (инвестиции, регуляция, кадры, бизнес)",
    "hype": "Желтуха (слухи, утечки, неподтверждённые данные, курьёзы)",
}

SYSTEM_PROMPT = """Ты редактор профессионального ежедневного издания об AI «Нейрогазета».
Твой голос: факты и конкретика, без метафор, без восхищения, без воды.
Правила:
- Пиши только то, что подтверждено источниками или явно помечай как слух.
- Не раздувай выпуск: если новостей мало — пиши мало.
- Дедупликация: если одна новость в нескольких источниках — одна запись с полем duplicate_note.
- Язык: русский, деловой стиль.
- importance: 9-10 главная новость дня, 6-8 важная, 1-5 краткая заметка.
- Возвращай ТОЛЬКО валидный JSON, без markdown-блоков и комментариев.
"""

USER_PROMPT = f"""Дата выпуска: {TODAY_STR}

Найди AI-новости за последние 24 часа по четырём рубрикам:
1. Модели — выпуски, обновления, бенчмарки языковых и мультимодальных моделей
2. Платформы — инструменты разработки, IDE с AI, API, новые продукты на базе моделей
3. Индустрия — инвестиции, регуляция, кадровые перестановки, слияния и поглощения
4. Желтуха — слухи, утечки, неподтверждённые данные, скандалы, курьёзы

Приоритет источников:
- Официальные блоги: openai.com/blog, anthropic.com/news, deepmind.google/blog, ai.meta.com/blog, mistral.ai/news, x.ai/blog, stability.ai/news
- Отраслевые СМИ: techcrunch.com, theverge.com, reuters.com, bloomberg.com, wsj.com, wired.com
- Открытый веб

Верни JSON строго в формате:
{{
  "date": "{TODAY_STR}",
  "published": false,
  "news": [
    {{
      "id": "уникальный-id-через-дефис",
      "section": "models|platforms|industry|hype",
      "headline": "Заголовок на русском",
      "subheadline": "Одно предложение — суть новости",
      "body": "Полный текст. Факты, цифры, прямые цитаты.",
      "importance": 1,
      "sources": [
        {{"title": "Название источника", "url": "https://...", "type": "official|media|rumor"}}
      ],
      "unconfirmed": false,
      "duplicate_note": null,
      "tags": {{
        "entities": ["название-сущности"],
        "sentiment": "positive|negative|neutral|rumor",
        "event": "release|update|shutdown|investment|regulation|leak"
      }}
    }}
  ]
}}
"""


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


def validate_and_fix(issue: dict) -> dict:
    """Проверяет структуру, устанавливает дефолты, генерирует ID."""
    valid_sections = set(SECTIONS)
    valid_sentiments = {"positive", "negative", "neutral", "rumor"}
    valid_events = {"release", "update", "shutdown", "investment", "regulation", "leak"}
    valid_source_types = {"official", "media", "rumor"}

    seen_ids: set[str] = set()
    clean_news = []

    for item in issue.get("news", []):
        if not item.get("headline"):
            continue

        # Section
        if item.get("section") not in valid_sections:
            item["section"] = "industry"

        # ID
        raw_id = item.get("id") or make_id(item["headline"], item["section"], issue["date"])
        uid = raw_id
        suffix = 2
        while uid in seen_ids:
            uid = f"{raw_id}-{suffix}"
            suffix += 1
        item["id"] = uid
        seen_ids.add(uid)

        # Importance
        try:
            imp = int(item.get("importance", 5))
            item["importance"] = max(1, min(10, imp))
        except (TypeError, ValueError):
            item["importance"] = 5

        # Defaults
        item.setdefault("subheadline", "")
        item.setdefault("body", "")
        item.setdefault("unconfirmed", False)
        item.setdefault("duplicate_note", None)

        # Sources
        sources = item.get("sources", []) or []
        clean_sources = []
        for s in sources:
            if isinstance(s, dict) and s.get("url"):
                s["type"] = s.get("type") if s.get("type") in valid_source_types else "media"
                clean_sources.append(s)
        item["sources"] = clean_sources

        # Tags
        tags = item.get("tags") or {}
        tags.setdefault("entities", [])
        if tags.get("sentiment") not in valid_sentiments:
            tags["sentiment"] = "neutral"
        if tags.get("event") not in valid_events:
            tags["event"] = "update"
        item["tags"] = tags

        clean_news.append(item)

    issue["news"] = clean_news
    issue["date"] = issue.get("date") or TODAY_STR
    issue.setdefault("published", False)
    return issue


# ── Сборка через API ─────────────────────────────────────────────────────────

def collect_news() -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY не задан")

    client = anthropic.Anthropic(api_key=api_key)

    print(f"[collect] Запрос к {MODEL} с web_search_20250305…", flush=True)

    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": USER_PROMPT}],
    )

    # Извлекаем текстовый контент из ответа
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    # Парсим JSON
    text = text.strip()
    # Убираем возможные markdown-блоки
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    issue = json.loads(text)
    return validate_and_fix(issue)


# ── Основная логика ──────────────────────────────────────────────────────────

def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[collect] Дата выпуска: {TODAY_STR}", flush=True)

    try:
        issue = collect_news()
    except json.JSONDecodeError as e:
        print(f"[error] Не удалось распарсить JSON: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    count = len(issue.get("news", []))
    print(f"[collect] Собрано новостей: {count}", flush=True)

    # Сохраняем выпуск
    out_path = DATA_DIR / f"{TODAY_STR}.json"
    out_path.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[collect] Сохранено: {out_path}", flush=True)

    # Обновляем latest.json
    LATEST_FILE.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[collect] Обновлён: {LATEST_FILE}", flush=True)

    # Обновляем индекс архива
    update_index(TODAY_STR, count, published=False)
    print(f"[collect] Индекс обновлён", flush=True)


if __name__ == "__main__":
    main()
