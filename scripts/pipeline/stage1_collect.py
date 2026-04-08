#!/usr/bin/env python3
"""
Этап 1: Сбор новостей из RSS и scrape-источников.
Вход:  sources.yaml
Выход: docs/data/{date}_raw.json

Не использует Claude. Можно запускать отдельно.
"""
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from common import (
    DATA_DIR, TODAY_STR, CUTOFF_24H, RAW_LIMIT,
    is_vague_url, clean_text, parse_date, extract_date_from_url,
    load_sources, load_recent_issues, get_prev_urls, log,
)

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NeuroGazeta/1.0)",
    "Accept": "text/html,application/rss+xml,application/xml,*/*",
}


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _http_get(url: str, retries: int = 2) -> requests.Response | None:
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

def fetch_rss(source: dict) -> list[dict]:
    url, name = source["url"], source["name"]
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

        if not pub_dt or pub_dt < CUTOFF_24H:
            continue
        title = (getattr(entry, "title", "") or "").strip()
        if not title:
            continue
        link = getattr(entry, "link", "") or ""
        if is_vague_url(link):
            continue
        summary = clean_text(
            getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        )[:500]
        result.append({
            "title":     title,
            "url":       link,
            "source":    name,
            "published": pub_dt.date().isoformat(),
            "summary":   summary,
            "priority":  source.get("priority", 2),
        })

    log.info("RSS %s: %d статей", name, len(result))
    return result


# ── Scrape ────────────────────────────────────────────────────────────────────

def scrape_page(source: dict) -> list[dict]:
    url, name = source["url"], source["name"]
    try:
        resp = _http_get(url)
    except Exception as e:
        log.warning("Scrape %s — ошибка: %s", name, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict] = []
    seen_urls: set[str] = set()

    # 1. JSON-LD (самый точный)
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
                    (main.get("@id") if isinstance(main, dict) else None) or ""
                ).strip()
                if not link.startswith("http"):
                    link = urljoin(url, link)
                if not title or not link or link in seen_urls or is_vague_url(link):
                    continue
                pub_dt = parse_date(item.get("datePublished") or item.get("dateModified") or "")
                if not pub_dt:
                    pub_dt = extract_date_from_url(link)
                if pub_dt and pub_dt < CUTOFF_24H:
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

    # 2. Fallback: h1/h2/h3
    for heading in soup.find_all(["h1", "h2", "h3"]):
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
        if is_vague_url(link):
            continue
        seen_urls.add(link)

        pub_dt = None
        block  = heading.find_parent(["article", "div", "li", "section"])
        if block:
            time_tag = block.find("time")
            if time_tag:
                pub_dt = parse_date(time_tag.get("datetime", "") or time_tag.get_text())
            if not pub_dt:
                for elem in block.find_all(["span", "p", "div"], limit=15):
                    cls = " ".join(elem.get("class") or [])
                    if any(k in cls.lower() for k in ("date", "time", "publish", "meta", "ago")):
                        pub_dt = parse_date(elem.get_text(strip=True))
                        if pub_dt:
                            break
        if not pub_dt:
            pub_dt = extract_date_from_url(link)
        if pub_dt and pub_dt < CUTOFF_24H:
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


# ── Сбор и дедупликация ───────────────────────────────────────────────────────

def collect_all(sources: list[dict]) -> list[dict]:
    rss_sources    = [s for s in sources if s.get("type") == "rss"]
    scrape_sources = [s for s in sources if s.get("type") == "scrape"]
    all_items: list[dict] = []
    counts: dict[str, int] = {}

    if rss_sources:
        with ThreadPoolExecutor(max_workers=min(10, len(rss_sources))) as ex:
            for src, items in zip(rss_sources, ex.map(fetch_rss, rss_sources)):
                counts[src["name"]] = len(items)
                all_items.extend(items)

    if scrape_sources:
        with ThreadPoolExecutor(max_workers=min(10, len(scrape_sources))) as ex:
            for src, items in zip(scrape_sources, ex.map(scrape_page, scrape_sources)):
                counts[src["name"]] = len(items)
                all_items.extend(items)

    empty  = [n for n, c in counts.items() if c == 0]
    active = {n: c for n, c in counts.items() if c > 0}
    if empty:
        log.warning("0 статей от %d источников: %s", len(empty), ", ".join(empty))
    if active:
        log.info("Активные: %s", ", ".join(f"{n}({c})" for n, c in sorted(active.items(), key=lambda x: -x[1])))
    return all_items


def deduplicate(articles: list[dict]) -> list[dict]:
    seen_urls:   set[str] = set()
    seen_titles: set[str] = set()
    result = []
    for a in articles:
        url = a.get("url", "").split("?")[0].rstrip("/")
        title_key = re.sub(r"\W+", " ", a.get("title", "").lower()).strip()[:120]
        if (url and url in seen_urls) or title_key in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        if title_key:
            seen_titles.add(title_key)
        result.append(a)
    return result


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_cache = DATA_DIR / f"{TODAY_STR}_raw.json"

    config  = load_sources()
    sources = config.get("sources", [])
    log.info("Источников: %d", len(sources))

    # Загружаем историю для фильтрации дублей
    prev_urls = get_prev_urls(load_recent_issues())
    log.info("Предыдущие выпуски: %d URL для исключения", len(prev_urls))

    raw_items = collect_all(sources)
    raw_items = deduplicate(raw_items)
    log.info("После дедупликации: %d статей", len(raw_items))

    if prev_urls:
        before = len(raw_items)
        raw_items = [a for a in raw_items if a.get("url", "").split("?")[0].rstrip("/") not in prev_urls]
        log.info("После исключения дублей с историей: %d → %d", before, len(raw_items))

    raw_items.sort(key=lambda a: a.get("priority", 2))
    raw_items = raw_items[:RAW_LIMIT]

    raw_cache.write_text(json.dumps(raw_items, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Этап 1 готов: %d статей → %s", len(raw_items), raw_cache)


if __name__ == "__main__":
    main()
