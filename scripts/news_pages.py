#!/usr/bin/env python3
"""
Страницы-превью для отдельных новостей:  docs/n/<id>.html

Зачем: ссылка на новость в выпуске выглядит как /?date=…#id, а всё после «#»
до сервера не доходит — Telegram, VK и другие мессенджеры видят только общую
главную. Здесь у каждой новости своя статичная страница с OG-тегами
(заголовок и подзаголовок новости), которая сразу перекидывает читателя
на новость в выпуске.

Генерируется при деплое (.github/workflows/deploy.yml) из всех docs/data/<дата>.json,
в git не хранится (docs/n/ в .gitignore) — поэтому правки из редактора
и добавленные вручную новости попадают в превью автоматически.

Только стандартная библиотека. Картинка превью общая: docs/og-news.png.

    python3 scripts/news_pages.py            # все выпуски
    python3 scripts/news_pages.py 2026-11-06 # только эти даты
"""
import json
import re
import sys
from datetime import date as date_cls
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "docs" / "data"
OUT_DIR = REPO / "docs" / "n"

SITE_URL = "https://нейрогазета.рф"
IMAGE_URL = f"{SITE_URL}/og-news.png"
IMAGE_SIZE = 600  # квадрат — Telegram показывает его маленькой иконкой справа, как у каналов

SECTION_LABELS = {
    "models": "Модели",
    "platforms": "Платформы",
    "industry": "Индустрия",
    "content": "Нейроконтент",
    "art": "Искусство",
    "hype": "Желтуха",
}

ISSUE_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def fmt_date(d: str) -> str:
    try:
        x = date_cls.fromisoformat(d)
        return f"{x.day} {MONTHS[x.month - 1]} {x.year}"
    except ValueError:
        return d


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:—–-")
    return cut + "…"


def build_page(item: dict, date: str) -> str:
    nid = item["id"]
    headline = clip(item.get("headline") or "Нейрогазета", 200)
    # Описание: подзаголовок; если его нет — начало текста
    description = clip(item.get("subheadline") or item.get("body") or "", 300)
    section = SECTION_LABELS.get(item.get("section"), "")
    kicker = " · ".join(x for x in (section, fmt_date(date)) if x)
    if item.get("unconfirmed"):
        kicker += " · не подтверждено"

    page_url = f"{SITE_URL}/n/{nid}.html"
    target = f"/?date={date}#{nid}"

    e = lambda s: escape(s, quote=True)  # noqa: E731
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{e(headline)} — Нейрогазета</title>
  <meta name="description" content="{e(description)}"/>
  <link rel="canonical" href="{e(page_url)}"/>
  <meta property="og:site_name" content="Нейрогазета"/>
  <meta property="og:type" content="article"/>
  <meta property="og:locale" content="ru_RU"/>
  <meta property="og:url" content="{e(page_url)}"/>
  <meta property="og:title" content="{e(headline)}"/>
  <meta property="og:description" content="{e(description)}"/>
  <meta property="og:image" content="{IMAGE_URL}"/>
  <meta property="og:image:width" content="{IMAGE_SIZE}"/>
  <meta property="og:image:height" content="{IMAGE_SIZE}"/>
  <meta property="article:published_time" content="{e(date)}"/>
  <meta property="article:section" content="{e(section)}"/>
  <meta name="twitter:card" content="summary"/>
  <meta name="twitter:title" content="{e(headline)}"/>
  <meta name="twitter:description" content="{e(description)}"/>
  <meta name="twitter:image" content="{IMAGE_URL}"/>
  <meta http-equiv="refresh" content="0; url={e(target)}"/>
  <script>location.replace({json.dumps(target)});</script>
  <style>
    body {{ margin: 0; padding: 3rem 1.25rem; background: #f5f0e8; color: #1a1a18;
           font: 16px/1.5 Georgia, serif; }}
    main {{ max-width: 40rem; margin: 0 auto; }}
    small {{ font: 12px/1.4 "Courier New", monospace; letter-spacing: .06em;
             text-transform: uppercase; color: #6b6860; }}
    h1 {{ font-size: 1.6rem; line-height: 1.2; margin: .5rem 0 1rem; }}
    a {{ color: #c0392b; }}
  </style>
</head>
<body>
  <main>
    <small>Нейрогазета · {e(kicker)}</small>
    <h1>{e(headline)}</h1>
    <p>{e(description)}</p>
    <p><a href="{e(target)}">Читать в выпуске →</a></p>
  </main>
</body>
</html>
"""


def issue_files(dates):
    if dates:
        return [DATA_DIR / f"{d}.json" for d in dates]
    return sorted(p for p in DATA_DIR.iterdir() if ISSUE_FILE.match(p.name))


def main(argv):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    seen = {}
    for path in issue_files(argv):
        try:
            issue = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            print(f"[WARN] {path.name}: {err}")
            continue
        date = issue.get("date") or path.stem
        for item in issue.get("news") or []:
            if item.get("tier") == "changelog" or item.get("section") == "changelog":
                continue
            nid = str(item.get("id") or "")
            if not SAFE_ID.match(nid):
                skipped += 1
                print(f"[WARN] {date}: пропущен id {nid!r}")
                continue
            if nid in seen and seen[nid] != date:
                print(f"[WARN] id {nid} есть в {seen[nid]} и {date} — беру {date}")
            seen[nid] = date
            out = OUT_DIR / f"{nid}.html"
            html = build_page(item, date)
            # Пишем только изменившиеся — FTP-деплой сравнивает содержимое,
            # а лишняя перезапись не нужна и локально
            if not out.exists() or out.read_text(encoding="utf-8") != html:
                out.write_text(html, encoding="utf-8")
            written += 1
    print(f"[INFO] Страниц новостей: {written} в {OUT_DIR.relative_to(REPO)}"
          + (f", пропущено: {skipped}" if skipped else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
