#!/usr/bin/env python3
"""
Генерирует OG-превью для выпуска:
  docs/data/{date}_preview.png   — картинка 1200×630
  docs/preview/{date}.html       — страница с OG-тегами → редирект на выпуск
"""
import json
from datetime import date as date_cls
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO        = Path(__file__).parent.parent
DATA_DIR    = REPO / "docs" / "data"
PREVIEW_DIR = REPO / "docs" / "preview"
FONTS_DIR   = Path(__file__).parent / "fonts"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

FONT_PLAYFAIR      = str(FONTS_DIR / "PlayfairDisplay.ttf")
FONT_PLAYFAIR_IT   = str(FONTS_DIR / "PlayfairDisplay-Italic.ttf")
FONT_SANS          = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_SANS_BOLD     = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

W, H = 1200, 630

# Цвета в стиле сайта
BG       = (250, 249, 245)   # кремовый фон (как газетная бумага)
INK      = (12,  12,  14)    # почти чёрный
GREY     = (100, 100, 100)
RULE     = (12,  12,  14)    # линии

SITE_URL = "https://нейрогазета.рф"


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fmt_date(date_str):
    try:
        d = date_cls.fromisoformat(date_str)
        months = ["января","февраля","марта","апреля","мая","июня",
                  "июля","августа","сентября","октября","ноября","декабря"]
        return f"{d.day} {months[d.month-1]} {d.year}"
    except Exception:
        return date_str


def generate_image(date: str, headline: str, subheadline: str) -> Path:
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    PAD = 64  # отступы по бокам

    # ── Шапка ────────────────────────────────────────────────────────────────
    f_masthead = ImageFont.truetype(FONT_SANS_BOLD, 18)
    f_date     = ImageFont.truetype(FONT_SANS, 18)

    draw.text((PAD, 44), "НЕЙРОГАЗЕТА", font=f_masthead, fill=INK)

    date_str = fmt_date(date)
    date_bbox = draw.textbbox((0, 0), date_str, font=f_date)
    draw.text((W - PAD - (date_bbox[2] - date_bbox[0]), 44), date_str, font=f_date, fill=GREY)

    # Тонкая линия над шапкой
    draw.rectangle([(PAD, 36), (W - PAD, 38)], fill=RULE)

    # Линия под шапкой
    draw.rectangle([(PAD, 72), (W - PAD, 74)], fill=RULE)
    draw.rectangle([(PAD, 77), (W - PAD, 78)], fill=RULE)

    # ── Заголовок ─────────────────────────────────────────────────────────────
    f_headline = ImageFont.truetype(FONT_PLAYFAIR, 62)
    lines = wrap_text(draw, headline, f_headline, W - PAD * 2)

    y = 108
    for line in lines[:4]:
        draw.text((PAD, y), line, font=f_headline, fill=INK)
        y += 72

    # ── Подзаголовок ──────────────────────────────────────────────────────────
    if subheadline:
        f_sub = ImageFont.truetype(FONT_PLAYFAIR_IT, 26)
        sub_lines = wrap_text(draw, subheadline, f_sub, W - PAD * 2)
        y += 8
        for line in sub_lines[:2]:
            draw.text((PAD, y), line, font=f_sub, fill=GREY)
            y += 36

    # Линия перед подвалом
    draw.rectangle([(PAD, H - 60), (W - PAD, H - 58)], fill=RULE)

    # ── Подвал ────────────────────────────────────────────────────────────────
    f_footer = ImageFont.truetype(FONT_SANS, 18)
    draw.text((PAD, H - 46), "Ежедневный AI-дайджест", font=f_footer, fill=GREY)

    out = DATA_DIR / f"{date}_preview.png"
    img.save(out, "PNG", optimize=True)
    print(f"[INFO] Превью: {out}")
    return out


def generate_html(date: str, headline: str, subheadline: str, img_path: Path) -> Path:
    img_url   = f"{SITE_URL}/data/{img_path.name}"
    issue_url = f"{SITE_URL}/?date={date}"
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8"/>
  <title>{headline}</title>
  <meta property="og:type"         content="article"/>
  <meta property="og:url"          content="{issue_url}"/>
  <meta property="og:title"        content="{headline}"/>
  <meta property="og:description"  content="{subheadline}"/>
  <meta property="og:image"        content="{img_url}"/>
  <meta property="og:image:width"  content="1200"/>
  <meta property="og:image:height" content="630"/>
  <meta name="twitter:card"        content="summary_large_image"/>
  <meta name="twitter:image"       content="{img_url}"/>
  <meta http-equiv="refresh" content="0; url={issue_url}"/>
</head>
<body><p>Перенаправление… <a href="{issue_url}">Нейрогазета — {date}</a></p></body>
</html>"""
    out = PREVIEW_DIR / f"{date}.html"
    out.write_text(html, encoding="utf-8")
    print(f"[INFO] Preview HTML: {out}")
    return out


def main():
    latest = DATA_DIR / "latest.json"
    if not latest.exists():
        print("[ERROR] latest.json не найден")
        return

    data = json.loads(latest.read_text(encoding="utf-8"))
    date = data.get("date", "")
    news = data.get("news", [])

    hero = next((n for n in news if n.get("tier") == "hero"), None) or (news[0] if news else None)
    if not hero:
        print("[WARN] Нет новостей для превью")
        return

    headline    = hero.get("headline", "Нейрогазета")
    subheadline = hero.get("subheadline", "")

    img_path = generate_image(date, headline, subheadline)
    generate_html(date, headline, subheadline, img_path)


if __name__ == "__main__":
    main()
