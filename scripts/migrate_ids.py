#!/usr/bin/env python3
"""
Одноразово: переводит id новостей во всех выпусках на формат <дата>-<номер>
(hero — №1, остальные по порядку в выпуске), как теперь делает пайплайн.
Карточка changelog не трогается. Повторный запуск ничего не меняет.

    python3 scripts/migrate_ids.py          # пробный прогон
    python3 scripts/migrate_ids.py --write
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "docs" / "data"
ISSUE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def renumber(issue: dict) -> int:
    date = issue["date"]
    news = [n for n in issue.get("news") or [] if n.get("tier") != "changelog"]
    ordered = sorted(news, key=lambda n: n.get("tier") != "hero")
    changed = 0
    for i, n in enumerate(ordered, 1):
        new = f"{date}-{i}"
        if n.get("id") != new:
            n["id"] = new
            changed += 1
    return changed


def main(write: bool):
    files = sorted(p for p in DATA.iterdir() if ISSUE.match(p.name))
    latest_date = json.loads((DATA / "latest.json").read_text(encoding="utf-8")).get("date")
    total = 0
    for path in files:
        issue = json.loads(path.read_text(encoding="utf-8"))
        changed = renumber(issue)
        total += changed
        if changed and write:
            path.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
        if issue["date"] == latest_date and write:
            (DATA / "latest.json").write_text(
                json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{'Записано' if write else 'Будет изменено'}: {total} id в {len(files)} выпусках")


if __name__ == "__main__":
    main("--write" in sys.argv)
