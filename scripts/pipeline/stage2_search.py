#!/usr/bin/env python3
"""
Этап 2: Веб-поиск через claude CLI (WebSearch tool).
Вход:  sources.yaml → search_queries
Выход: docs/data/{date}_search.json  (dict: section → текст с результатами)

Использует токены подписки через `claude --print --allowedTools WebSearch`.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (
    DATA_DIR, TODAY_STR, CUTOFF_24H, CUTOFF_DATE_STR, WINDOW_STR,
    is_vague_url, parse_date, extract_date_from_url,
    load_sources, run_claude, log,
)


def build_search_prompt(query: str) -> str:
    dated_query = f"{query} {CUTOFF_DATE_STR}"
    return (
        f"Найди актуальные AI-новости по запросу: «{dated_query}».\n"
        f"Только публикации за период {WINDOW_STR}.\n"
        "Материалы вне этого периода не включай.\n"
        "Перечисли 3-5 результатов строго в формате одной строки каждый:\n"
        "ЗАГОЛОВОК | URL конкретной статьи | ДАТА (YYYY-MM-DD) | краткое описание\n"
        "Требования: URL должен вести на конкретную статью, не на тег-страницу, "
        "раздел или главную. Если конкретного URL нет — пропусти результат.\n"
        "Только строки в указанном формате, без пояснений."
    )


def filter_search_lines(text: str, section: str) -> list[str]:
    """Отбрасывает строки с размытым URL или датой вне окна сбора."""
    valid = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        article_url = parts[1].strip()
        if is_vague_url(article_url):
            log.debug("%s search: отброшен vague URL: %r", section, article_url)
            continue
        if len(parts) >= 3:
            dt = parse_date(parts[2].strip()) or extract_date_from_url(article_url)
            if dt and dt < CUTOFF_24H:
                # date-only парсится как midnight UTC — сравниваем только дату
                if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                    if dt.date() < CUTOFF_24H.date():
                        log.info("%s search: старая статья (%s): %s", section, parts[2], parts[0][:60])
                        continue
                else:
                    log.info("%s search: старая статья (%s): %s", section, parts[2], parts[0][:60])
                    continue
        valid.append(line)
    return valid


def search_section(section: str, queries: list[str]) -> str:
    blocks: list[str] = []
    for i, query in enumerate(queries):
        if i > 0:
            log.info("%s: пауза 30с перед следующим запросом…", section)
            time.sleep(30)
        log.info("%s: поиск по запросу «%s»", section, query[:60])
        try:
            text = run_claude(
                build_search_prompt(query),
                allowed_tools=["WebSearch"],
                model="claude-sonnet-4-6",
                timeout=300,
                retries=2,
            )
            valid = filter_search_lines(text, section)
            if valid:
                blocks.append("\n".join(valid))
                log.info("%s: %d валидных результатов", section, len(valid))
            else:
                log.warning("%s: 0 валидных результатов для «%s»", section, query[:60])
        except Exception as e:
            log.warning("%s: ошибка поиска «%s»: %s", section, query[:60], e)

    return "\n\n---\n\n".join(blocks)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    search_cache = DATA_DIR / f"{TODAY_STR}_search.json"

    config = load_sources()
    search_queries: dict[str, list[str]] = config.get("search_queries") or {}

    if not search_queries:
        log.info("Нет search_queries в sources.yaml — пропускаем этап 2")
        search_cache.write_text("{}", encoding="utf-8")
        return

    log.info("Секций с поиском: %s", list(search_queries.keys()))
    results: dict[str, str] = {}

    for i, (section, queries) in enumerate(search_queries.items()):
        if not queries:
            continue
        if i > 0:
            log.info("Пауза 30с перед следующей секцией…")
            time.sleep(30)
        results[section] = search_section(section, queries)
        log.info("%s: итого %d символов", section, len(results[section]))

    search_cache.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Этап 2 готов → %s", search_cache)


if __name__ == "__main__":
    main()
