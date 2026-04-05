#!/usr/bin/env python3
"""
Нейрогазета — генератор статистики по сущностям.

Сканирует все выпуски в docs/data/, агрегирует упоминания
брендов/моделей с sentiment. Результат: docs/data/stats.json.
"""

import json
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "docs" / "data"
STATS_FILE = DATA_DIR / "stats.json"


def load_all_issues() -> list[dict]:
    """Загружает все выпуски (YYYY-MM-DD.json), исключая служебные файлы."""
    issues = []
    for path in sorted(DATA_DIR.glob("????-??-??.json")):
        try:
            issues.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return issues


def build_stats(issues: list[dict]) -> dict:
    """Строит агрегированную статистику по сущностям."""
    # entity -> {mentions, positive, negative, neutral, first_seen, last_seen, by_month}
    entities: dict[str, dict] = defaultdict(lambda: {
        "mentions": 0,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "first_seen": None,
        "last_seen": None,
        "by_month": defaultdict(lambda: {
            "mentions": 0, "positive": 0, "negative": 0, "neutral": 0,
        }),
    })

    for issue in issues:
        date = issue.get("date", "")
        month = date[:7]  # "2026-04"

        for item in issue.get("news", []):
            sentiment = (item.get("tags") or {}).get("sentiment", "neutral")
            # rumor → neutral для статистики
            if sentiment == "rumor":
                sentiment = "neutral"

            for entity in (item.get("tags") or {}).get("entities", []):
                _record(entities, entity, sentiment, date, month)

            # related items
            for rel in item.get("related") or []:
                rel_sentiment = rel.get("sentiment", "neutral")
                if rel_sentiment not in ("positive", "negative", "neutral"):
                    rel_sentiment = "neutral"
                for entity in rel.get("entities") or []:
                    _record(entities, entity, rel_sentiment, date, month)

    # Сортируем по количеству упоминаний
    sorted_entities = dict(
        sorted(entities.items(), key=lambda kv: kv[1]["mentions"], reverse=True)
    )

    # Конвертируем defaultdict в обычные dict для JSON
    for data in sorted_entities.values():
        data["by_month"] = dict(data["by_month"])

    return {
        "updated": issues[-1]["date"] if issues else None,
        "total_issues": len(issues),
        "total_entities": len(sorted_entities),
        "entities": sorted_entities,
    }


def _record(entities: dict, entity: str, sentiment: str, date: str, month: str):
    """Записывает одно упоминание сущности."""
    e = entities[entity]
    e["mentions"] += 1
    e[sentiment] += 1
    if e["first_seen"] is None or date < e["first_seen"]:
        e["first_seen"] = date
    if e["last_seen"] is None or date > e["last_seen"]:
        e["last_seen"] = date
    e["by_month"][month]["mentions"] += 1
    e["by_month"][month][sentiment] += 1


def generate():
    issues = load_all_issues()
    if not issues:
        print("Нет выпусков для анализа")
        return
    stats = build_stats(issues)
    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats['total_entities']} сущностей из {stats['total_issues']} выпусков → {STATS_FILE}")


if __name__ == "__main__":
    generate()
