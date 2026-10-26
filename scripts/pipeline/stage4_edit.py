#!/usr/bin/env python3
"""
Этап 4: Финальная редактура через claude CLI (Opus).
Вход:  {date}_filtered.txt, {date}_search.json
Выход: {date}.json, latest.json, обновление index.json

Модель — MODEL_EDIT из common.py (Opus).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (
    MODEL_EDIT,
    DATA_DIR, LATEST_FILE, TODAY_STR, WINDOW_STR,
    load_recent_issues, get_prev_headlines,
    finalize_news, update_index, run_claude, build_changelog_item, log,
    events_prompt,
)
from entities import prompt_reference

# Правила тегирования — общие для этапа 4 и для перетегирования истории (scripts/retag.py)
TAGGING_RULES = """Теги сущностей (tags.entities и related[].entities):
- 1–5 главных участников новости: компании, модели, продукты, люди, госорганы, страны, законы.
  Не темы и не отрасли: «роботы», «стартапы», «бенчмарки», «open source», «AI agents» — не сущности.
- Если в новости есть конкретный участник — хотя бы одна сущность обязательна.
- Если сущность есть в справочнике в конце промпта — пиши её ровно так, как там.
- Новой сущности в справочнике нет — тогда компании, продукты, модели и люди в оригинальном написании
  (Sam Altman, не «Сэм Альтман»), страны и госорганы — по-русски (США, Пентагон, Еврокомиссия).

Тональность:
- tags.sentiment — общий тон новости: positive | negative | neutral.
- tags.entity_sentiment — тональность ОТДЕЛЬНО для каждой сущности из tags.entities: как новость сказывается
  именно на этом участнике. Ключи — ровно те же имена, что в tags.entities.
  * positive — новость в его пользу: релиз, рост, сделка, выигранный суд, полученные инвестиции, похвала;
  * negative — против него: иск или штраф против него, утечка, сбой, отток людей, запрет, критика;
  * neutral — упомянут без последствий для него: регулятор, площадка, контрагент, источник данных.
  Пример: «OpenAI подала в суд на xAI за кражу кода» → {"OpenAI": "neutral", "xAI": "negative"}.
  Пример: «Anthropic купила Coefficient Bio» → {"Anthropic": "positive", "Coefficient Bio": "positive"}.
- Слух — это не тональность. Неподтверждённая новость: "unconfirmed": true, а тональность — обычная.

Тип события (tags.event) — ровно один код из списка «Типы событий» в конце промпта:
- Подходит несколько — выбирай главное действие новости («Meta купила стартап и наняла его команду» → acquisition).
- update — только изменение уже существующего продукта. Прогноз, интервью, мнение, обзор или подборка
  уже существующих разработок — statement, а не update.
- release — только если новое стало доступно: продукт, модель, код или веса. Описание метода, архитектуры
  или эксперимента без доступа к результату — research.
- incident — только если что-то произошло: сбой, взлом, уязвимость, утечка данных, реальный вред, мошенничество.
  Критика, расследование СМИ, скандал вокруг решения компании без такого происшествия — criticism.
  Иск или расследование властей — legal.
- leak — утечка сведений о планах компании. Утечка пользовательских данных или взлом — incident.
- Ничего не подходит — other. Не подставляй update по умолчанию."""

# Рубрики и правила content/art — общие для этапа 4 и для перетегирования истории (scripts/retag.py)
RUBRIC_RULES = """Рубрики:
- models    — выпуски и обновления языковых и мультимодальных моделей
- platforms — инструменты, IDE, API, продукты на базе AI
- industry  — инвестиции, регуляция, кадры, бизнес
- content   — «Нейроконтент»: нейросети, которыми пользуются нейрокреаторы, — генерация фото, видео,
              3D-моделей, миров и игр; их релизы и обновления, бесплатные генераторы и тарифы, скидки на подписки
- art       — «Искусство»: выставки, нейромузеи, фестивали, конкурсы и премии ИИ-искусства (мир и Россия)
- hype      — слухи, утечки, неподтверждённые данные, курьёзы

Рубрики content и art — правила:
- Релиз или обновление генератора изображений, видео, 3D, миров или игр (Midjourney, Flux, Ideogram, Recraft,
  Imagen/Nano Banana, GPT-image, Seedream, Firefly, Kandinsky, Veo, Sora, Kling, Runway, Hailuo, Seedance, Pika,
  Luma, Higgsfield, Meshy, Tripo, Genie, World Labs и т.п.) идёт в content, а не в models/platforms.
  Исключение — универсальная мультимодальная модель, где генерация лишь одна из функций: она остаётся в models.
- Деньги, суды и кадры вокруг этих компаний (раунд Runway, иск к Midjourney) — по обычным правилам: industry/hype.
- Скидки и промо: только если объявлены самим сервисом и есть размер скидки или цена и срок. Без этого — не включай.
  Всегда section "content", tier "compact", event "promo", sentiment "neutral". Никаких «выгодно» и «успейте» — только условия.
- Бесплатное: пиши, в чём именно ограничение (лимит генераций в день, разрешение, водяной знак, открытые веса).
  «Бесплатно» без условий не пиши. event "promo", если это акция; "release", если вышел новый бесплатный инструмент.
- Выставки, музеи, фестивали, конкурсы ИИ-искусства: section "art", event "exhibition".
  Если в источнике нет площадки и дат (или срока приёма работ) — не включай.
- Поле "details" (только для content и art, необязательно): одна строка до 120 символов с практическими условиями.
  Выставка: «Город, площадка · даты». Скидка: «−40% на годовой план · до 31 октября». Бесплатное: «50 генераций в день · 1024 px».
  Пиши только то, что явно есть в источнике. Нечего написать — не добавляй поле."""

EDIT_SYSTEM = (
    """Ты редактор профессионального ежедневного издания об AI «Нейрогазета».
Голос: факты и конкретика, без метафор, без восхищения, без воды.

Правила:
- Включай новость только если у неё есть конкретный URL статьи и дата публикации ИСТОЧНИКА в пределах периода сбора, указанного в начале промпта. Проверяй дату публикации самой статьи-источника — не дату когда о ней написали агрегаторы или дайджесты. Новости старше периода сбора — не включай, даже если они кажутся важными или были найдены через веб-поиск. Тег-страницы, главные страницы сайтов и URL без конкретного материала — не источники. Лучше меньше новостей чем одна выдуманная.
- Не используй дайджесты, агрегаторы и «round-up» статьи как источники — только первичные публикации.
- Приоритет источников: инфоповод должен исходить от первоисточника — официального блога компании, исследовательской публикации, регулятора. Статья СМИ «компания X сделала Y» — не инфоповод, если есть оригинальная публикация X. В таком случае: source = официальный пост X, статья СМИ — в related. Медиа включай как основной source только если нет официального источника (расследование, утечка, независимый анализ).
- В поле body НИКОГДА не пиши дату если она не подтверждена явно в тексте источника. Не подставляй дату выпуска как дату события.
- Дедупликация: несколько источников об одном событии — одна запись, поле duplicate_note.
- Целевой диапазон: 5-7 новостей на рубрику. Меньше честных лучше, чем больше выдуманных.
- Если новостей по рубрике больше 7 — оставь топ-7 по importance, остальные отсеки.
- Покрытие секций: старайся включить новости во ВСЕ 6 рубрик. Не допускай ситуации, когда целая рубрика пуста, если для неё есть материалы.
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

"""
    + TAGGING_RULES
    + """

"""
    + RUBRIC_RULES
    + """

Формат ответа:
{
  "news": [
    {
      "id": "section-uniqueid",
      "section": "models|platforms|industry|content|art|hype",
      "headline": "Заголовок на русском",
      "subheadline": "Одно предложение — суть новости",
      "body": "Полный текст. Факты, цифры, прямые цитаты — сверх того, что уже сказано в заголовке.",
      "tier": "hero|regular|compact",
      "sources": [{"title": "Название", "url": "https://...", "type": "official|media|rumor"}],
      "related": [{"title": "Заголовок менее значимой новости", "url": "https://...", "entities": ["Company"], "sentiment": "positive|negative|neutral"}],
      "unconfirmed": false,
      "duplicate_note": null,
      "details": "только для content и art: «Москва, ГЭС-2 · 20 окт — 15 янв» или «−40% на год · до 31 октября»; иначе поле не добавлять",
      "tags": {
        "entities": ["OpenAI", "GPT-5.5"],
        "sentiment": "positive|negative|neutral",
        "entity_sentiment": {"OpenAI": "positive", "GPT-5.5": "positive"},
        "event": "код из списка «Типы событий»"
      }
    }
  ]
}"""
)


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
        "Обеспечь покрытие всех 6 рубрик (models, platforms, industry, content, art, hype). "
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
                system=(
                    EDIT_SYSTEM
                    + "\n\nТипы событий:\n" + events_prompt()
                    + "\n\nСправочник сущностей:\n" + prompt_reference()
                ),
                model=MODEL_EDIT,
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
