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
        "Статьи с датой «unknown» — дата не определена автоматически. Включай их ТОЛЬКО если по заголовку "
        "и URL очевидно, что это свежая новость. При сомнении — убирай. "
        "Разнообразие: следи за тем, чтобы в отфильтрованный список попадали статьи от РАЗНЫХ компаний "
        "и источников. Если от одной компании много пресс-релизов — оставь 2-3 самых значимых. "
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
        model="claude-sonnet-4-6",
        timeout=600,
        retries=3,
    )

    if not text.strip():
        log.error("Фильтрация вернула пустой ответ")
        sys.exit(1)

    filtered_out.write_text(text, encoding="utf-8")
    log.info("Этап 3 готов: ~%d строк → %s", text.count("\n"), filtered_out)


if __name__ == "__main__":
    main()
