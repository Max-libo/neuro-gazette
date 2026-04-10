#!/usr/bin/env python3
"""
Этап 4: Финальная редактура через claude CLI (Opus).
Вход:  {date}_filtered.txt, {date}_search.json
Выход: {date}.json, latest.json, обновление index.json

Использует Opus — лучшее качество, без доплаты на подписке Max.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (
    DATA_DIR, LATEST_FILE, TODAY_STR, WINDOW_STR,
    load_recent_issues, get_prev_headlines,
    finalize_news, update_index, run_claude, build_changelog_item, log,
)

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
- Тематическая дедупликация: если несколько статей описывают одну тенденцию — объедини их в одну аналитическую заметку с несколькими sources.
- tier: у каждой новости поле "tier" — одно из трёх значений:
  * "hero" — РОВНО ОДНА на весь выпуск. Самое значимое событие дня. Развёрнутый body.
  * "regular" — основные новости. Полноценный body и подзаголовок.
  * "compact" — мелкие заметки. Короткий body, на фронтенде подзаголовок скрыт под катом.
  Непроверенные новости (unconfirmed: true) не могут быть hero.
- Качество body: текст body должен содержать факты, цифры или контекст, которых НЕТ в headline и subheadline. Если body просто пересказывает заголовок другими словами — перепиши, добавив детали из источника. «Подробности в публикации» — не body.
- Группировка по компании: если от одной компании/темы много новостей — оформи 1-2 самые важные как полноценные статьи. Остальные менее значимые помести в поле "related" главной статьи (массив объектов {title, url}).
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
      "tier": "hero|regular|compact",
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


def build_edit_prompt(
    filtered_text: str,
    search_texts: dict[str, str],
    prev_headlines: list[str],
) -> str:
    lines = [
        f"Дата выпуска: {TODAY_STR}. Период сбора: {WINDOW_STR}. "
        "Новости опубликованные вне этого периода не включать.\n"
    ]
    if prev_headlines:
        lines.append(
            "=== ОПУБЛИКОВАНО В ПРЕДЫДУЩИХ ВЫПУСКАХ ===\n"
            "Правила:\n"
            "• Если новость — ТОЧНЫЙ повтор той же истории без новых фактов — НЕ включай.\n"
            "• Если новость — ПРОДОЛЖЕНИЕ или новый угол уже освещённой темы (новые факты, апдейт, развитие) — "
            "включай, но оформи особым образом:\n"
            "  - headline: «Продолжение: [суть темы кратко]» (не копируй старый заголовок)\n"
            "  - subheadline: null\n"
            "  - body: краткий текст с новыми фактами (1-2 предложения)\n"
            "  - tier: \"compact\"\n"
            "  - duplicate_note: точный заголовок оригинальной новости из списка ниже\n"
            "Список предыдущих заголовков:"
        )
        for h in prev_headlines:
            lines.append(f"- {h}")
        lines.append("")
    lines.append(
        "=== ОТФИЛЬТРОВАННЫЕ МАТЕРИАЛЫ (формат: заголовок | URL | источник | дата публикации) ===\n"
        "ВАЖНО: последнее поле — реальная дата публикации источника. "
        "Включай статью только если эта дата попадает в период сбора."
    )
    lines.append(filtered_text)
    for section, text in search_texts.items():
        if text:
            lines.append(f"\n=== ВЕБ-ПОИСК (рубрика {section}) ===")
            lines.append(text)
    lines.append(
        "\nОформи финальный выпуск по схеме JSON. "
        "Обеспечь покрытие всех 4 рубрик (models, platforms, industry, hype). "
        "Источники со ★ — приоритетные."
    )
    return "\n".join(lines)


def extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return text.strip()


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filtered_path = DATA_DIR / f"{TODAY_STR}_filtered.txt"
    search_cache  = DATA_DIR / f"{TODAY_STR}_search.json"
    out_path      = DATA_DIR / f"{TODAY_STR}.json"

    if not filtered_path.exists():
        log.error("Нет %s — сначала запустите этап 3", filtered_path)
        sys.exit(1)

    filtered_text = filtered_path.read_text(encoding="utf-8")
    search_texts: dict[str, str] = {}
    if search_cache.exists():
        search_texts = json.loads(search_cache.read_text(encoding="utf-8"))

    prev_headlines = get_prev_headlines(load_recent_issues())
    prompt = build_edit_prompt(filtered_text, search_texts, prev_headlines)

    news_list: list[dict] = []
    last_err = ""
    for attempt in range(1, 4):
        hint = (
            f"\n\nВАЖНО: предыдущий ответ не был валидным JSON ({last_err}). "
            "Верни ТОЛЬКО JSON, без пояснений."
        ) if last_err else ""
        try:
            log.info("Редактура, попытка %d/3…", attempt)
            text = run_claude(
                prompt + hint,
                system=EDIT_SYSTEM,
                model="claude-opus-4-6",
                timeout=600,
                retries=1,  # retry-логика внешняя
            )
            data = json.loads(extract_json(text))
            news_list = data.get("news", [])
            log.info("Получено %d новостей", len(news_list))
            break
        except json.JSONDecodeError as e:
            last_err = str(e)
            log.warning("Невалидный JSON (попытка %d): %s", attempt, e)
        except Exception as e:
            log.error("Ошибка редактуры: %s", e)
            sys.exit(1)
    else:
        log.error("Редактура: все попытки исчерпаны")
        sys.exit(1)

    all_news = finalize_news(news_list)
    all_news.append(build_changelog_item())

    if not all_news:
        log.error("Выпуск пуст после редактуры — файл не записан")
        sys.exit(1)

    issue = {"date": TODAY_STR, "published": False, "news": all_news}
    out_path.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_FILE.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    update_index(TODAY_STR, len(all_news))

    # Статистика по сущностям (опционально)
    try:
        import importlib.util, os
        stats_path = Path(__file__).parent.parent / "stats.py"
        if stats_path.exists():
            spec = importlib.util.spec_from_file_location("stats", stats_path)
            stats = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(stats)
            stats.generate()
    except Exception as e:
        log.warning("Статистика не обновлена: %s", e)

    log.info("Этап 4 готов: %d новостей → %s", len(all_news), out_path)


if __name__ == "__main__":
    main()
