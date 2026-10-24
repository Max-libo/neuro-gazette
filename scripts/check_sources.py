#!/usr/bin/env python3
"""
Проверка источников из sources.yaml — без Claude, только HTTP.

Использует те же функции, что и боевой этап 1 (fetch_rss / scrape_page /
фильтр по ключевым словам), но с окном 30 дней вместо 24 часов, чтобы
понять: источник жив и отдаёт настоящие статьи, или мёртв / отдаёт мусор.

Запуск:
  python3 scripts/check_sources.py              — все источники
  python3 scripts/check_sources.py content      — только рубрика content
  python3 scripts/check_sources.py content models

В GitHub Actions пишет таблицу в Summary запуска (вкладка Actions → запуск).
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "pipeline"))
import stage1_collect as s1  # noqa: E402
from common import load_sources, log  # noqa: E402

WINDOW_DAYS = 30
s1.CUTOFF_24H = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)


def check(source: dict) -> dict:
    name, url, kind = source["name"], source["url"], source.get("type")
    row = {"name": name, "section": source.get("section", ""), "type": kind, "url": url,
           "status": "", "count": 0, "kw_note": "", "newest": "", "samples": []}
    try:
        resp = s1._http_get(url, retries=1)
        row["status"] = f"HTTP {resp.status_code}"
    except Exception as e:
        msg = str(e)
        row["status"] = "ОШИБКА: " + (msg[:80] + "…" if len(msg) > 80 else msg)
        return row

    src = dict(source)
    src["_last_url"] = ""          # без закладки — смотрим всё, что есть на странице
    keywords = src.pop("keywords", None)
    items = s1.fetch_rss(src) if kind == "rss" else s1.scrape_page(src)
    total = len(items)
    if keywords:
        src["keywords"] = keywords
        items = s1.apply_keyword_filter(src, items)
        row["kw_note"] = f"{len(items)} из {total} про ИИ"
    row["count"] = len(items)
    dates = sorted((a["published"] for a in items if a["published"] != "unknown"), reverse=True)
    row["newest"] = dates[0] if dates else ("unknown" if items else "")
    row["samples"] = [(a["title"][:70], a["url"]) for a in items[:3]]
    return row


def verdict(row: dict) -> str:
    if row["status"].startswith("ОШИБКА"):
        return "❌ недоступен"
    if row["count"] == 0:
        return "⚠️ 0 статей" if not row["kw_note"] else "⚠️ 0 про ИИ за 30 дней"
    if row["newest"] == "unknown":
        return "🟡 даты не определяются"
    return "✅"


def main() -> None:
    sections = set(sys.argv[1:])
    sources = load_sources().get("sources", [])
    if sections:
        sources = [s for s in sources if s.get("section") in sections]
    log.info("Проверяю %d источников (окно %d дней)…", len(sources), WINDOW_DAYS)

    with ThreadPoolExecutor(max_workers=10) as ex:
        rows = list(ex.map(check, sources))

    lines = [
        f"## Проверка источников — окно {WINDOW_DAYS} дней",
        "",
        "| Итог | Источник | Рубрика | Тип | Ответ | Статей | Свежая | Ключевые слова |",
        "|---|---|---|---|---|---|---|---|",
    ]
    order = {"❌": 0, "⚠️": 1, "🟡": 2, "✅": 3}
    rows.sort(key=lambda r: (order.get(verdict(r).split()[0], 9), r["section"], r["name"]))
    for r in rows:
        lines.append(f"| {verdict(r)} | {r['name']} | {r['section']} | {r['type']} | {r['status']} | "
                     f"{r['count']} | {r['newest']} | {r['kw_note']} |")

    lines += ["", "### Что реально отдают (первые 3 статьи)", ""]
    for r in rows:
        if not r["samples"]:
            continue
        lines.append(f"**{r['name']}**")
        for title, url in r["samples"]:
            lines.append(f"- [{title}]({url})")
        lines.append("")

    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
