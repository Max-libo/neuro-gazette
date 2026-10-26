#!/usr/bin/env python3
"""
Этап 3: Фильтрация сырых статей через claude CLI.
Вход:  {date}_raw.json, {date}_search.json
Выход: {date}_filtered.txt  (список: заголовок | URL | источник | дата)

Использует Sonnet (быстро, экономит контекст подписки).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (
    MODEL_FAST,
    DATA_DIR, TODAY_STR, WINDOW_STR,
    load_recent_issues, get_prev_headlines,
    run_claude, log,
)


def build_filter_prompt(
    articles: list[dict],
    search_texts: dict[str, str],
    prev_headlines: list[str],
) -> str:
    lines = [
        "Ты редактор профессионального AI-издания. Из этого списка оставь все статьи которые "
        "могут быть интересны AI-специалисту — анонсы моделей, обновления продуктов, новые функции, "
        "события индустрии, исследования, резонансные истории. Убирай только явный маркетинг без "
        "новостной ценности, туториалы типа 'как использовать X', и материалы старше периода сбора. "
        "ВАЖНО: у каждой статьи указана дата публикации (последнее поле). Убирай статьи у которых "
        "дата публикации источника выходит за пределы периода сбора — даже если заголовок звучит актуально. "
        "Статьи с датой «unknown» — дата не определена автоматически. "
        "Для источников ★ (официальные блоги компаний): включай до 2 верхних статей с unknown датой — "
        "они стоят первыми на странице, значит скорее всего свежие. "
        "Для остальных источников с unknown датой: включай ТОЛЬКО если по заголовку и URL очевидно свежая новость, при сомнении убирай.\n\n"
        "ИЕРАРХИЯ ИСТОЧНИКОВ (строго соблюдай):\n"
        "1. Официальные блоги и сайты компаний (помечены ★) — основные носители инфоповода. "
        "Если компания сама опубликовала анонс, релиз или исследование — это первичный источник, включай. "
        "Из официальных источников одной компании оставь до 3 самых значимых.\n"
        "2. Профессиональные AI-СМИ (The Decoder, TechCrunch, The Verge и др.) — включай ТОЛЬКО если "
        "они освещают тему, по которой нет официального источника в списке. "
        "Статья «X выпустила модель Y» в TechCrunch — лишняя, если есть официальный пост X про модель Y.\n"
        "3. Аналитика, расследования, мнения в СМИ — включай если добавляют контекст или угол, "
        "которого нет в официальных источниках (регуляция, скандал, исследование третьей стороны).\n"
        "4. Веб-поиск — только если тема полностью отсутствует среди RSS/scrape источников.\n\n"
        "НЕЙРОКОНТЕНТ (отдельная рубрика: нейросети, которыми пользуются нейрокреаторы; "
        "читатель — креатор и продюсер, а не только инженер). Оставляй, а не выкидывай как маркетинг:\n"
        "• релизы и обновления генераторов изображений (Midjourney, Flux, Ideogram, Recraft, Imagen/Nano Banana, "
        "GPT-image, Seedream, Firefly, Krea, Leonardo, Kandinsky и др.) — новые версии, режимы, функции, цены;\n"
        "• генераторы видео (Veo, Sora, Kling, Runway, Hailuo, Seedance, Pika, Luma, Higgsfield, Wan и др.);\n"
        "• генерация 3D-моделей (Meshy, Tripo, Rodin, Hunyuan3D и др.), миров и игр (Genie, World Labs и др.);\n"
        "• бесплатные генераторы и бесплатные тарифы — только если доступ реально бесплатный "
        "(есть ежедневный/месячный лимит или открытые веса), а не триал на пару генераций;\n"
        "• скидки и промо на подписки творческих сервисов — только если объявлены самим сервисом (официальный сайт/блог/соцсеть), "
        "есть конкретный размер скидки или цена и срок действия. Купонные агрегаторы, реферальные ссылки, "
        "«лучшие нейросети со скидкой» — убирай.\n"
        "Обзоры-подборки («10 лучших генераторов»), SEO-листиклы и гайды «как сделать картинку в X» — убирай.\n\n"
        "ИСКУССТВО (отдельная рубрика): выставки, нейромузеи, фестивали, конкурсы и премии ИИ-искусства "
        "в мире и в России — только если указаны площадка и даты (или дата открытия, срок приёма работ). "
        "Общие статьи «ИИ и искусство» без события — убирай.\n\n"
        "Разнообразие: следи за тем, чтобы в список попадали статьи от РАЗНЫХ компаний. "
        "Лучше оставить лишнее чем потерять важное. "
        "Верни список в виде: заголовок | URL | источник | дата. Без пояснений.\n",
        f"Период сбора: {WINDOW_STR}. Статей: {len(articles)}.\n",
    ]
    for a in articles:
        pri_mark = " ★" if a.get("priority", 2) == 1 else ""
        summary  = a.get("summary", "").strip()[:200]
        summary_part = f" | {summary}" if summary else ""
        lines.append(f"{a['title']} | {a['url']} | {a['source']}{pri_mark} | {a['published']}{summary_part}")

    for section, text in search_texts.items():
        if text:
            lines.append(f"\n=== ВЕБ-ПОИСК ({section}) ===")
            lines.append(text)

    if prev_headlines:
        lines.append("\n=== УЖЕ ОПУБЛИКОВАНО — НЕ ВКЛЮЧАТЬ ===")
        for h in prev_headlines:
            lines.append(f"- {h}")

    return "\n".join(lines)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_cache    = DATA_DIR / f"{TODAY_STR}_raw.json"
    search_cache = DATA_DIR / f"{TODAY_STR}_search.json"
    filtered_out = DATA_DIR / f"{TODAY_STR}_filtered.txt"

    if not raw_cache.exists():
        log.error("Нет %s — сначала запустите этап 1", raw_cache)
        sys.exit(1)

    articles: list[dict] = json.loads(raw_cache.read_text(encoding="utf-8"))
    search_texts: dict[str, str] = {}
    if search_cache.exists():
        search_texts = json.loads(search_cache.read_text(encoding="utf-8"))

    prev_headlines = get_prev_headlines(load_recent_issues())
    log.info("Фильтрация: %d статей + %d секций поиска", len(articles), len(search_texts))

    prompt = build_filter_prompt(articles, search_texts, prev_headlines)
    text = run_claude(
        prompt,
        model=MODEL_FAST,
    )

    if not text.strip():
        log.error("Фильтрация вернула пустой ответ")
        sys.exit(1)

    filtered_out.write_text(text, encoding="utf-8")
    log.info("Этап 3 готов: ~%d строк → %s", text.count("\n"), filtered_out)


if __name__ == "__main__":
    main()
