#!/usr/bin/env python3
"""
Генерирует OG-превью для выпуска:
  docs/data/{date}_preview.png   — картинка 1200×630
  docs/preview/{date}.html       — страница с OG-тегами → редирект на выпуск
"""
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).parent.parent
DATA_DIR = REPO / "docs" / "data"
PREVIEW_DIR = REPO / "docs" / "preview"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

FONT_SERIF_BOLD  = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_SANS        = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_SANS_BOLD   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

W, H = 1200, 630
BG        = (15, 15, 20)        # почти чёрный
ACCENT    = (220, 180, 80)      # золото
WHITE     = (255, 255, 255)
GREY      = (160, 160, 160)
DARKGREY  = (60, 60, 65)

SITE_URL  = "https://neurogazeta.ru"


def wrap_text(draw, text, font, max_width):
    """Оборачивает текст по пикселям, возвращает список строк."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def generate_image(date: str, headline: str, subheadline: str, section: str) -> Path:
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Левая золотая полоса
    draw.rectangle([(0, 0), (6, H)], fill=ACCENT)

    # Горизонтальная линия-разделитель
    draw.rectangle([(40, 90), (W - 40, 93)], fill=DARKGREY)
    draw.rectangle([(40, 96), (W - 40, 98)], fill=ACCENT)

    # Шапка: НЕЙРОГАЗЕТА
    f_masthead = ImageFont.truetype(FONT_SANS_BOLD, 32)
    draw.text((40, 32), "НЕЙРОГАЗЕТА", font=f_masthead, fill=ACCENT)

    # Дата справа
    f_date = ImageFont.truetype(FONT_SANS, 24)
    from datetime import date as date_cls
    try:
        d = date_cls.fromisoformat(date)
        months = ["января","февраля","марта","апреля","мая","июня",
                  "июля","августа","сентября","октября","ноября","декабря"]
        date_str = f"{d.day} {months[d.month-1]} {d.year}"
    except Exception:
        date_str = date
    bbox = draw.textbbox((0, 0), date_str, font=f_date)
    draw.text((W - 40 - (bbox[2] - bbox[0]), 38), date_str, font=f_date, fill=GREY)

    # Заголовок hero-новости
    f_headline = ImageFont.truetype(FONT_SERIF_BOLD, 52)
    lines = wrap_text(draw, headline, f_headline, W - 120)
    y = 130
    line_h = 64
    for line in lines[:4]:  # максимум 4 строки
        draw.text((40, y), line, font=f_headline, fill=WHITE)
        y += line_h

    # Подзаголовок
    if subheadline:
        f_sub = ImageFont.truetype(FONT_SANS, 28)
        sub_lines = wrap_text(draw, subheadline, f_sub, W - 120)
        y += 16
        for line in sub_lines[:2]:
            draw.text((40, y), line, font=f_sub, fill=GREY)
            y += 38

    # Нижняя строка
    draw.rectangle([(0, H - 56), (W, H - 56)], fill=DARKGREY)
    f_footer = ImageFont.truetype(FONT_SANS, 22)
    draw.text((40, H - 42), "neurogazeta.ru", font=f_footer, fill=ACCENT)
    draw.text((40 + 200, H - 42), "— ежедневный AI-дайджест", font=f_footer, fill=GREY)

    out = DATA_DIR / f"{date}_preview.png"
    img.save(out, "PNG", optimize=True)
    print(f"[INFO] Превью: {out}")
    return out


def generate_html(date: str, headline: str, subheadline: str, img_path: Path) -> Path:
    img_url = f"{SITE_URL}/data/{img_path.name}"
    issue_url = f"{SITE_URL}/?date={date}"
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8"/>
  <title>{headline}</title>
  <meta property="og:type"        content="article"/>
  <meta property="og:url"         content="{issue_url}"/>
  <meta property="og:title"       content="{headline}"/>
  <meta property="og:description" content="{subheadline}"/>
  <meta property="og:image"       content="{img_url}"/>
  <meta property="og:image:width"  content="1200"/>
  <meta property="og:image:height" content="630"/>
  <meta name="twitter:card"        content="summary_large_image"/>
  <meta name="twitter:image"       content="{img_url}"/>
  <meta http-equiv="refresh" content="0; url={issue_url}"/>
</head>
<body>
  <p>Перенаправление… <a href="{issue_url}">Нейрогазета — {date}</a></p>
</body>
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
    section     = hero.get("section", "")

    img_path = generate_image(date, headline, subheadline, section)
    generate_html(date, headline, subheadline, img_path)


if __name__ == "__main__":
    main()
