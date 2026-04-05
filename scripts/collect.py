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
from urllib.parse import urljoin, urlparse

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

MODEL_FAST = "claude-sonnet-4-6"   # поиск, фильтрация
MODEL_EDIT = "claude-opus-4-6"    # финальная редактура

MSK        = timezone(timedelta(hours=3))
NOW        = datetime.now(MSK)
# Фиксированное окно: с 07:00 МСК вчера до 07:00 МСК сегодня.
# Если запуск до 07:00 — берём предыдущее окно (позавчера→вчера).
_today_0700 = NOW.replace(hour=7, minute=0, second=0, microsecond=0)
_window_end = _today_0700 if NOW >= _today_0700 else _today_0700 - timedelta(days=1)
CUTOFF_24H      = (_window_end - timedelta(hours=24)).astimezone(timezone.utc)
TODAY           = _window_end.date()
TODAY_STR       = TODAY.isoformat()
CUTOFF_DATE_STR = (TODAY - timedelta(days=1)).isoformat()  # дата начала окна сбора
# Читаемый диапазон для промптов: "2026-04-04 07:00 МСК — 2026-04-05 07:00 МСК"
_cutoff_msk     = CUTOFF_24H.astimezone(MSK)
WINDOW_STR      = f"{_cutoff_msk.strftime('%Y-%m-%d %H:%M')} МСК — {_window_end.strftime('%Y-%m-%d %H:%M')} МСК"

SECTIONS  = ["models", "platforms", "industry", "hype"]
RAW_LIMIT = 200  # максимум статей на вход Claude

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

# Паттерны URL, которые никогда не указывают на конкретную статью
_ALWAYS_VAGUE_URL = re.compile(
    r"/(?:tags?|categories?|topics?|labels?|sections?)/",
    re.IGNORECASE,
)
_VAGUE_INDEX_URL = re.compile(
    r"/(?:blog|news|articles?)/?$",
    re.IGNORECASE,
)
_AGGREGATE_URL = re.compile(
    r"/(?:latest|roundup|digest|weekly|daily|this-week|updates?)(?:[/-]|$)",
    re.IGNORECASE,
)


def _is_vague_url(url: str) -> bool:
    """True если URL — тег-страница, раздел или главная, а не конкретная статья."""
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


def _extract_date_from_url(url: str) -> datetime | None:
    """Извлекает дату из URL вида /2026/04/05/... или /2026/04/..."""
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
            return datetime(int(m.group(1)), int(m.group(2)), 1,
                            tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


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
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return None


# ── HTTP с retry ─────────────────────────────────────────────────────────────

def _http_get(url: str, retries: int = 2) -> requests.Response | None:
    """GET-запрос с простым retry при сетевых ошибках."""
    for attempt in range(1, retries + 2):
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=15)
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt > retries:
                raise
            log.debug("HTTP retry %d/%d для %s: %s", attempt, retries, url, e)
    return None


# ── RSS ───────────────────────────────────────────────────────────────────────

def fetch_rss_feed(source: dict, cutoff: datetime) -> list[dict]:
    url  = source["url"]
    name = source["name"]
    try:
        resp = _http_get(url)
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

        if not pub_dt:
            continue
        if pub_dt < cutoff:
            continue

        title = (getattr(entry, "title", "") or "").strip()
        if not title:
            continue

        link    = getattr(entry, "link", "") or ""
        if _is_vague_url(link):
            continue
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
            "priority":  source.get("priority", 2),
        })

    log.info("RSS %s: %d статей", name, len(result))
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
        resp = _http_get(url)
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
                if _is_vague_url(link):
                    continue
                pub_dt = _parse_date(
                    item.get("datePublished") or item.get("dateModified") or ""
                )
                if not pub_dt:
                    pub_dt = _extract_date_from_url(link)
                if pub_dt and pub_dt < cutoff:
                    continue
                seen_urls.add(link)
                results.append({
                    "title":     title,
                    "url":       link,
                    "source":    name,
                    "published": pub_dt.date().isoformat() if pub_dt else "unknown",
                    "summary":   (item.get("description") or "")[:500],
                    "priority":  source.get("priority", 2),
                })
        except Exception:
            pass

    if results:
        log.info("Scrape %s (JSON-LD): %d статей", name, len(results))
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
        if _is_vague_url(link):
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

        if not pub_dt:
            pub_dt = _extract_date_from_url(link)
        if pub_dt and pub_dt < cutoff:
            continue

        results.append({
            "title":     title,
            "url":       link,
            "source":    name,
            "published": pub_dt.date().isoformat() if pub_dt else "unknown",
            "summary":   "",
            "priority":  source.get("priority", 2),
        })

    log.info("Scrape %s (HTML): %d статей", name, len(results))
    return results


# ── Сбор всех источников ──────────────────────────────────────────────────────

def collect_from_sources(sources: list[dict], cutoff: datetime) -> list[dict]:
    """Параллельно собирает материалы из RSS и scrape-источников."""
    rss_sources    = [s for s in sources if s.get("type") == "rss"]
    scrape_sources = [s for s in sources if s.get("type") == "scrape"]

    all_items: list[dict] = []

    if rss_sources:
        with ThreadPoolExecutor(max_workers=min(10, len(rss_sources))) as ex:
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
        title_key = re.sub(r"\W+", " ", a.get("title", "").lower()).strip()[:120]
        if (url and url in seen_urls) or title_key in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        if title_key:
            seen_titles.add(title_key)
        result.append(a)
    return result


# ── Hype через веб-поиск ──────────────────────────────────────────────────────

async def fetch_via_search(client: AsyncAnthropic, section: str, queries: list[str]) -> str:
    """Запросы к Claude с web_search для заданной рубрики."""
    blocks: list[str] = []

    for i, query in enumerate(queries):
        if i > 0:
            log.info("%s search: пауза 30с перед следующим запросом…", section)
            await asyncio.sleep(30)
        dated_query = f"{query} {CUTOFF_DATE_STR}"
        try:
            text = await _api_call(
                client,
                model=MODEL_FAST,
                max_tokens=1000,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Найди актуальные AI-новости по запросу: «{dated_query}». "
                        f"Только публикации за период {WINDOW_STR}. "
                        "Материалы вне этого периода не включай. "
                        "Перечисли 3-5 результатов строго в формате одной строки каждый:\n"
                        "ЗАГОЛОВОК | URL конкретной статьи | ДАТА (YYYY-MM-DD) | краткое описание\n"
                        "Требования: URL должен вести на конкретную статью, не на тег-страницу, "
                        "раздел или главную. Если конкретного URL нет — пропусти результат. "
                        "Только строки в указанном формате, без пояснений."
                    ),
                }],
            )
            valid_lines = []
            for line in text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 2:
                    continue
                article_url = parts[1].strip()
                if _is_vague_url(article_url):
                    log.debug("%s search: отброшен результат без конкретного URL: %r", section, article_url)
                    continue
                # Валидация даты: отбрасываем результаты вне окна сбора
                if len(parts) >= 3:
                    search_dt = _parse_date(parts[2].strip())
                    if not search_dt:
                        search_dt = _extract_date_from_url(article_url)
                    if search_dt and search_dt < CUTOFF_24H:
                        log.info("%s search: отброшена старая статья (%s): %s",
                                 section, parts[2].strip(), parts[0].strip()[:60])
                        continue
                valid_lines.append(line)
            if valid_lines:
                blocks.append("\n".join(valid_lines))
        except Exception as e:
            log.warning("%s search (%r) ошибка: %s", section, query, e)

    result = "\n\n---\n\n".join(blocks)
    log.info("%s web-search: %d символов", section, len(result))
    return result


# ── Промпты ──────────────────────────────────────────────────────────────────

EDIT_SYSTEM = """Ты редактор профессионального ежедневного издания об AI «Нейрогазета».
Голос: факты и конкретика, без метафор, без восхищения, без воды.

Правила:
- Включай новость только если у неё есть конкретный URL статьи и дата публикации ИСТОЧНИКА в пределах периода сбора, указанного в начале промпта. Проверяй дату публикации самой статьи-источника — не дату когда о ней написали агрегаторы или дайджесты. Новости старше периода сбора — не включай, даже если они кажутся важными или были найдены через веб-поиск. Тег-страницы, главные страницы сайтов и URL без конкретного материала — не источники. Лучше меньше новостей чем одна выдуманная.
- Не используй дайджесты, агрегаторы и «round-up» статьи как источники — только первичные публикации.
- В поле body НИКОГДА не пиши дату если она не подтверждена явно в тексте источника. Не подставляй дату выпуска как дату события.
- Дедупликация: несколько источников об одном событии — одна запись, поле duplicate_note.
- Целевой диапазон: 5-7 новостей на рубрику. Меньше честных лучше, чем больше выдуманных.
- Если новостей по рубрике больше 7 — оставь топ-7 по importance, остальные отсеки.
- Покрытие секций: старайся включить новости во ВСЕ 4 рубрики. Не допускай ситуации, когда целая рубрика пуста, если для неё есть материалы.
- Разнообразие: если несколько пресс-релизов от одной компании — объедини связанные в одну новость с несколькими sources, а не создавай отдельные записи на каждый пресс-релиз.
- Тематическая дедупликация: если несколько статей описывают одну тенденцию (например, три новости про копирайт и AI) — объедини их в одну аналитическую заметку с несколькими sources, а не создавай отдельные записи.
- importance: РОВНО ОДНА новость в выпуске должна иметь importance 9 или 10 — это главная новость дня. Все остальные: 6-8 важные, 1-5 краткие заметки. Не ставь 9-10 более чем одной новости.
- Если новость помечена unconfirmed: true — её importance не может быть выше 6. Непроверенные новости не бывают главными.
- Качество body: текст body должен содержать факты, цифры или контекст, которых НЕТ в headline и subheadline. Если body просто пересказывает заголовок другими словами — перепиши, добавив детали из источника. «Подробности в публикации» — не body.
- Группировка по компании: если от одной компании/темы много новостей — оформи 1-2 самые важные как полноценные статьи. Остальные менее значимые помести в поле "related" главной статьи (массив объектов {title, url}). Это даст компактный блок «ещё N новостей» со ссылками, без потери информации. Не создавай отдельные записи на каждый мелкий пресс-релиз.
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
      "body": "Полный текст. Факты, цифры, прямые цитаты — сверх того, что уже сказано в заголовке.",
      "importance": 1,
      "sources": [{"title": "Название", "url": "https://...", "type": "official|media|rumor"}],
      "related": [{"title": "Заголовок менее значимой новости", "url": "https://...", "entities": ["Company"], "sentiment": "positive|negative|neutral"}],
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


def build_filter_prompt(articles: list[dict], search_texts: dict[str, str], prev_headlines: list[str]) -> str:
    lines = [
        "Ты редактор профессионального AI-издания. Из этого списка оставь все статьи которые "
        "могут быть интересны AI-специалисту — анонсы моделей, обновления продуктов, новые функции, "
        "события индустрии, исследования, резонансные истории. Убирай только явный маркетинг без "
        "новостной ценности, туториалы типа 'как использовать X', и материалы старше периода сбора. "
        "ВАЖНО: у каждой статьи указана дата публикации (последнее поле). Убирай статьи у которых "
        "дата публикации источника выходит за пределы периода сбора — даже если заголовок звучит актуально. "
        "Статьи с датой «unknown» — дата не определена автоматически. Включай их ТОЛЬКО если по заголовку "
        "и URL очевидно, что это свежая новость. При сомнении — убирай. "
        "Разнообразие: следи за тем, чтобы в отфильтрованный список попадали статьи от РАЗНЫХ компаний "
        "и источников. Если от одной компании много пресс-релизов — оставь 2-3 самых значимых, "
        "а освободившееся место отдай другим компаниям �� темам. "
        "Лучше оставить лишнее чем потерять важное. "
        "Верни список в виде: заголовок | URL | источник | дата. Без пояснений.\n",
        f"Период сбора: {WINDOW_STR}. Статей: {len(articles)}.\n",
    ]
    for a in articles:
        pri = a.get("priority", 2)
        pri_mark = " ★" if pri == 1 else ""
        lines.append(f"{a['title']} | {a['url']} | {a['source']}{pri_mark} | {a['published']}")
    for section, text in search_texts.items():
        if text:
            lines.append(f"\n=== ВЕБ-ПОИСК ({section}) ===")
            lines.append(text)
    if prev_headlines:
        lines.append("\n=== УЖЕ ОПУБЛИКОВАНО ВЧЕРА — НЕ ВКЛЮЧАТЬ ===")
        for h in prev_headlines:
            lines.append(f"- {h}")
    return "\n".join(lines)


def build_edit_prompt(filtered_text: str, search_texts: dict[str, str], prev_headlines: list[str]) -> str:
    lines = [f"Дата выпуска: {TODAY_STR}. Период сбора: {WINDOW_STR}. Новости опубликованные вне этого периода не включать.\n"]
    if prev_headlines:
        lines.append("=== УЖЕ ОПУБЛИКОВАНО В ПРЕДЫДУЩЕМ ВЫПУСКЕ — НЕ ВКЛЮЧАТЬ ===")
        for h in prev_headlines:
            lines.append(f"- {h}")
        lines.append("")
    lines.append("=== ОТФИЛЬТРОВАННЫЕ МАТЕРИАЛЫ (формат: заголовок | URL | источник | дата публикации) ===")
    lines.append("ВАЖНО: последнее поле — реальная дата публикации источника. Включай статью только если эта дата попадает в период сбора. Дата выпуска и дата события — разные вещи.")
    lines.append(filtered_text)
    for section, text in search_texts.items():
        if text:
            lines.append(f"\n=== ВЕБ-ПОИСК (рубрика {section}) ===")
            lines.append(text)
    lines.append("\nОформи финальный выпуск по схеме JSON. Обеспечь покрытие всех 4 рубрик (models, platforms, industry, hype) — если для рубрики есть материалы, она не должна быть пустой. Источники со ★ — приоритетные.")
    return "\n".join(lines)


# ── Claude API ────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return text.strip()


async def _api_call(client: AsyncAnthropic, **kwargs) -> str:
    """Вызов Claude API с retry на rate-limit и API-ошибки. Возвращает текст ответа."""
    last_err = ""
    for attempt in range(1, 4):
        try:
            response = await client.messages.create(**kwargs)
            text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text" and b.text]
            return "\n".join(text_parts)
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
            last_err = str(e)
        except anthropic.APIError as e:
            last_err = str(e)
            log.warning("Ошибка API, попытка %d/3: %s", attempt, e)
            if attempt < 3:
                await asyncio.sleep(15)
    raise RuntimeError(f"Claude API: все попытки исчерпаны. Последняя ошибка: {last_err}")


async def filter_with_claude(
    client: AsyncAnthropic,
    articles: list[dict],
    search_texts: dict[str, str],
    prev_headlines: list[str],
) -> str:
    """Вызов 1: Claude выбирает 30-40 самых важных статей. Возвращает текст списка."""
    log.info("Вызов 1 — фильтрация (%d статей)…", len(articles))
    text = await _api_call(
        client,
        model=MODEL_FAST,
        max_tokens=8000,
        messages=[{"role": "user", "content": build_filter_prompt(articles, search_texts, prev_headlines)}],
    )
    log.info("Фильтрация: выбрано ~%d строк", text.count("\n"))
    return text


async def edit_with_claude(
    client: AsyncAnthropic,
    filtered_text: str,
    search_texts: dict[str, str],
    prev_headlines: list[str],
) -> list[dict]:
    """Вызов 2: Claude оформляет финальный выпуск в JSON."""
    log.info("Вызов 2 — редактура…")
    last_err = ""
    for attempt in range(1, 4):
        try:
            hint = (
                f"\n\nВАЖНО: предыдущий ответ не был валидным JSON ({last_err}). "
                "Верни ТОЛЬКО JSON, без пояснений."
            ) if last_err else ""

            text = await _api_call(
                client,
                model=MODEL_EDIT,
                max_tokens=8000,
                system=EDIT_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": build_edit_prompt(filtered_text, search_texts, prev_headlines) + hint,
                }],
            )
            data = json.loads(_extract_json(text))
            news = data.get("news", [])
            log.info("Редактура: оформлено %d новостей (попытка %d)", len(news), attempt)
            return news
        except json.JSONDecodeError as e:
            last_err = str(e)
            log.warning("Невалидный JSON, попытка %d/3: %s", attempt, e)
            if attempt < 3:
                await asyncio.sleep(10)
        except RuntimeError as e:
            log.error("%s", e)
            return []

    log.error("Редактура: все попытки исчерпаны")
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

    valid_sentiments_rel = {"positive", "negative", "neutral"}
    related = item.get("related") or []
    fixed_related = []
    for r in related:
        if not isinstance(r, dict) or not r.get("title") or not r.get("url") or not str(r["url"]).startswith("http"):
            continue
        fixed_related.append({
            "title": r["title"],
            "url": r["url"],
            "entities": r.get("entities") or [],
            "sentiment": r["sentiment"] if r.get("sentiment") in valid_sentiments_rel else "neutral",
        })
    item["related"] = fixed_related

    sources = item.get("sources") or []
    item["sources"] = [
        {**s, "type": s["type"] if s.get("type") in valid_source_types else "media"}
        for s in sources
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


# ── Предыдущие выпуски (7 дней) ──────────────────────────────────────────────

HISTORY_DAYS = 7


def load_recent_issues() -> list[dict]:
    """Загружает выпуски за последние HISTORY_DAYS дней (без сегодняшнего)."""
    issues = []
    for i in range(1, HISTORY_DAYS + 1):
        date_str = (TODAY - timedelta(days=i)).isoformat()
        path = DATA_DIR / f"{date_str}.json"
        if path.exists():
            try:
                issues.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
    return issues


def get_prev_urls(issues: list[dict]) -> set[str]:
    """Возвращает множество URL из списка выпусков."""
    urls = set()
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
    """Возвращает список заголовков из списка выпусков."""
    headlines = []
    for issue in issues:
        for item in issue.get("news", []):
            if item.get("headline"):
                headlines.append(item["headline"])
    return headlines


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
    search_queries = config.get("search_queries") or {}
    log.info("Источников: %d, секций с web-поиском: %s", len(sources), list(search_queries.keys()))

    async with AsyncAnthropic(api_key=anthropic_key) as client:
        await _run_pipeline(client, sources, search_queries)


async def _run_pipeline(
    client: AsyncAnthropic,
    sources: list[dict],
    search_queries: dict,
) -> None:
    # ── Загружаем выпуски за последние 7 дней ──────────────────────────────────
    recent_issues  = load_recent_issues()
    prev_urls      = get_prev_urls(recent_issues)
    prev_headlines = get_prev_headlines(recent_issues)
    log.info("История (%d выпусков): %d URL, %d заголовков для исключения",
             len(recent_issues), len(prev_urls), len(prev_headlines))

    # ── Этап 1: сбор материалов за 24ч ───────────────────────────────────────
    log.info("Сбор материалов (окно 24ч)…")
    raw_items = collect_from_sources(sources, CUTOFF_24H)
    raw_items = deduplicate_raw(raw_items)
    log.info("Собрано (24ч): %d статей", len(raw_items))

    # Исключаем статьи, URL которых уже есть в предыдущем выпуске
    if prev_urls:
        before = len(raw_items)
        raw_items = [a for a in raw_items if a.get("url", "").split("?")[0].rstrip("/") not in prev_urls]
        log.info("После исключения дублей с предыдущим выпуском: %d → %d статей", before, len(raw_items))

    raw_items.sort(key=lambda a: a.get("priority", 2))
    raw_items = raw_items[:RAW_LIMIT]

    # Кэшируем собранные статьи на случай сбоя Claude API
    raw_cache = DATA_DIR / f"{TODAY_STR}_raw.json"
    raw_cache.write_text(json.dumps(raw_items, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Сырые статьи сохранены: %s", raw_cache)

    # ── Этап 2: веб-поиск по секциям ─────────────────────────────────────────
    search_texts: dict[str, str] = {}
    for i, (section, queries) in enumerate(search_queries.items()):
        if queries:
            if i > 0:
                log.info("Пауза 30с перед веб-поиском секции %s…", section)
                await asyncio.sleep(30)
            log.info("Веб-поиск для секции %s…", section)
            search_texts[section] = await fetch_via_search(client, section, queries)

    if not raw_items and not any(search_texts.values()):
        log.error("Нет материалов — выпуск пустой")
        sys.exit(1)

    # ── Этап 3: Claude API — фильтрация + редактура ──────────────────────────
    filtered_text = await filter_with_claude(client, raw_items, search_texts, prev_headlines)
    if not filtered_text.strip():
        log.error("Фильтрация вернула пустой ответ")
        sys.exit(1)
    news_list = await edit_with_claude(client, filtered_text, search_texts, prev_headlines)

    seen_ids: set[str] = set()
    all_news = [n for item in news_list if (n := validate_and_fix(item, seen_ids))]

    # Потолок importance 6 для непроверенных новостей
    for n in all_news:
        if n.get("unconfirmed") and n["importance"] > 6:
            log.info("Снижен importance %d→6 для unconfirmed: %s", n["importance"], n["headline"][:60])
            n["importance"] = 6

    # Ровно одна hero-новость (importance >= 9): оставляем самую важную, остальные → 8
    heroes = [n for n in all_news if n["importance"] >= 9]
    if len(heroes) > 1:
        heroes.sort(key=lambda n: n["importance"], reverse=True)
        for n in heroes[1:]:
            log.info("Снижен importance %d→8 (лишний hero): %s", n["importance"], n["headline"][:60])
            n["importance"] = 8
    elif not heroes and all_news:
        top = max(all_news, key=lambda n: n["importance"])
        log.info("Повышен importance %d→9 (нет hero): %s", top["importance"], top["headline"][:60])
        top["importance"] = 9

    log.info("Итого новостей в выпуске: %d", len(all_news))

    if not all_news:
        log.error("Выпуск пуст после редактуры — файл не записан")
        sys.exit(1)

    issue    = {"date": TODAY_STR, "published": False, "news": all_news}
    out_path = DATA_DIR / f"{TODAY_STR}.json"
    out_path.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_FILE.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    update_index(TODAY_STR, len(all_news))
    log.info("Готово: %s", out_path)

    # ── Обновляем статистику по сущностям ────────────────────────────────────
    try:
        from stats import generate as generate_stats
        generate_stats()
    except Exception as e:
        log.warning("Не удалось обновить статистику: %s", e)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
