#!/usr/bin/env python3
"""
Перетегирование истории Нейрогазеты внешним исполнителем (GPT, Claude, человек).

    python scripts/retag.py export ПАПКА           # собрать набор: задание, справочник, входные файлы, check.py
    python scripts/retag.py apply  ПАПКА           # проверить ответы и показать, что изменится (ничего не пишет)
    python scripts/retag.py apply  ПАПКА --write   # записать теги в docs/data/*.json и пересчитать stats.json

Исполнитель читает ПАПКА/ЗАДАНИЕ.md, размечает input/*.json и кладёт ответы в output/*.json.
Правила разметки берутся из того же кода, что и в ежедневном пайплайне (TAGGING_RULES, EVENTS,
entities.yaml), поэтому история и новые выпуски размечаются по одним правилам.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pipeline"))

import common  # noqa: E402
from common import CREATIVE_SECTIONS, DATA_DIR, EVENTS, LATEST_FILE, SECTIONS, SENTIMENTS  # noqa: E402
from entities import directory, prompt_reference  # noqa: E402
from stage4_edit import RUBRIC_RULES, TAGGING_RULES  # noqa: E402

KIT_VERSION = 3
MOVES_FILE = "ПЕРЕНОСЫ.json"   # переносы новостей в творческие рубрики (content, art)


# ── Экспорт ───────────────────────────────────────────────────────────────────
def _issue_paths() -> list[Path]:
    return sorted(DATA_DIR.glob("????-??-??.json"))


def _export_item(item: dict) -> dict:
    tags = item.get("tags") or {}
    return {
        "id":          item["id"],
        "section":     item.get("section"),
        "unconfirmed": bool(item.get("unconfirmed")),
        "headline":    item.get("headline") or "",
        "subheadline": item.get("subheadline") or "",
        "body":        item.get("body") or "",
        "sources":     [s.get("title") or s.get("url") for s in item.get("sources") or []],
        "old": {
            "entities":  tags.get("entities") or [],
            "sentiment": tags.get("sentiment"),
            "event":     tags.get("event"),
        },
        "related": [
            {
                "url":           r.get("url"),
                "title":         r.get("title"),
                "old_entities":  r.get("entities") or [],
                "old_sentiment": r.get("sentiment"),
            }
            for r in item.get("related") or []
        ],
    }


def _reference_md() -> str:
    d = directory()
    lines = [
        "# Справочник сущностей",
        "",
        "Пиши сущность ровно так, как она записана слева. Варианты в скобках — это то же самое,",
        "их писать нельзя: «Nvidia» → **NVIDIA**, «Pentagon» → **Пентагон**, «Сэм Альтман» → **Sam Altman**.",
        "Сущности нет в справочнике — пиши её по правилу из задания; это нормально, справочник пополняется.",
        "",
    ]
    groups = [
        ("company", "Компании"), ("model", "Модели"), ("product", "Продукты"), ("person", "Люди"),
        ("gov", "Госорганы"), ("place", "Страны и регионы"), ("org", "Организации, медиа, вузы"),
        ("law", "Законы"), ("tech", "Технологии"),
    ]
    for t, title in groups:
        names = sorted((n for n, m in d.entities.items() if m["type"] == t), key=str.casefold)
        if not names:
            continue
        lines += [f"## {title}", ""]
        for n in names:
            meta = d.entities[n]
            extra = []
            if meta["parent"]:
                extra.append(f"входит в {meta['parent']}")
            if meta["aliases"]:
                extra.append("не писать: " + ", ".join(meta["aliases"]))
            lines.append(f"- **{n}**" + (f" — {'; '.join(extra)}" if extra else ""))
        lines.append("")
    stop = sorted(_stoplist(), key=str.casefold)
    lines += ["## Не сущности (темы) — не ставить", "", ", ".join(stop), ""]
    return "\n".join(lines)


def _stoplist() -> list[str]:
    import yaml
    raw = yaml.safe_load(directory().path.read_text(encoding="utf-8")) or {}
    return [str(x) for x in raw.get("stoplist") or []]


def _task_md(n_files: int, n_items: int, n_related: int) -> str:
    events = "\n".join(f"| `{code}` | {label} | {desc} |" for code, (label, desc) in EVENTS.items())
    return f"""# Задание: перетегировать архив «Нейрогазеты»

## Контекст

«Нейрогазета» — ежедневный русскоязычный дайджест новостей об AI. У каждой новости есть теги:
участники (сущности), тональность и тип события. По ним строится аналитика: какие компании
упоминаются, в каком тоне, какие события происходят.

Правила тегирования недавно поменялись: появилась тональность **для каждой сущности отдельно**,
слух больше не считается тональностью, типов событий стало {len(EVENTS)} вместо 6, появился справочник
сущностей. Новые выпуски уже размечаются по новым правилам. Нужно переразметить архив —
**{n_files} выпусков, {n_items} новостей, {n_related} связанных заметок** — по тем же правилам.

Тексты новостей не меняются. Меняются теги и — для части новостей — рубрика: недавно появились
рубрики «Нейроконтент» (`content`) и «Искусство» (`art`), и подходящие новости архива переезжают туда.

## Что в папке

| Путь | Что это |
|---|---|
| `ЗАДАНИЕ.md` | этот файл |
| `справочник.md` | канонические имена сущностей — писать ровно так |
| `input/ГГГГ-ММ-ДД.json` | новости одного выпуска: текст и старые теги (только читать) |
| `output/` | сюда класть ответы: по одному файлу на выпуск, с тем же именем, и `{MOVES_FILE}` |
| `check.py` | проверка ответов: `python3 check.py` (только стандартная библиотека) |
| `_kit/` | служебные данные для `check.py`, не трогать |

## Порядок работы

1. Прочитай этот файл и `справочник.md` целиком.
2. Бери выпуски по одному в порядке дат. Для каждого файла `input/X.json` создай `output/X.json`.
3. После каждого файла запусти `python3 check.py X` (например, `python3 check.py 2026-04-04`)
   и исправь ошибки. Предупреждения (⚠) — не ошибки, но посмотри на них.
4. Когда готовы все файлы — `python3 check.py` без аргументов должен показать
   «Ошибок нет» и «готово {n_files} из {n_files}».
5. Пройди архив ещё раз по рубрикам (раздел «Рубрики» ниже) и запиши переносы
   в `output/{MOVES_FILE}`. `python3 check.py` проверяет и его.
6. Напиши `output/ОТЧЁТ.md`: сколько сделано, какие были сомнения, какие новые сущности
   добавил (их нет в справочнике), что в правилах показалось неоднозначным. Коротко, списком.

Нельзя: менять файлы в `input/`, `_kit/` и `check.py`; создавать файлы вне `output/`;
трогать что-либо за пределами этой папки. Интернет не нужен — всё есть в тексте новости.

## Формат ответа: `output/ГГГГ-ММ-ДД.json`

```json
{{
  "date": "2026-04-04",
  "items": [
    {{
      "id": "industry-1a2b3c-2026-04-04",
      "entities": ["OpenAI", "xAI"],
      "entity_sentiment": {{"OpenAI": "neutral", "xAI": "negative"}},
      "sentiment": "negative",
      "event": "legal",
      "related": [
        {{"url": "https://…", "entities": ["xAI"], "sentiment": "negative"}}
      ],
      "note": "необязательно: сомнение для редактора"
    }}
  ]
}}
```

- `items` — **все** новости из входного файла, с теми же `id`, в том же порядке. Ни одной лишней.
- `entities` — список имён; `entity_sentiment` — ключи **ровно те же**, что в `entities`.
- `sentiment` — `positive` | `negative` | `neutral`. Никаких `rumor`.
- `event` — один код из таблицы ниже.
- `related` — **все** связанные заметки из входа с теми же `url`, в том же порядке.
  Для каждой: `entities` и одна `sentiment` (это короткие заметки, тональность у них одна на всё).
  Если у новости во входе `related: []` — в ответе тоже `[]`.
- `note` — только если есть реальное сомнение. Не пиши его «для галочки».
- Во входе есть `old` — старые теги. Это черновик, а не ответ: сущности часто можно оставить
  (приведя к справочнику), а тональность и тип события оцени заново по правилам ниже.
- `unconfirmed` во входе — для контекста. Если стоит `true`, это неподтверждённая новость:
  тональность и тип ставь как обычно (утечка о планах компании — `leak`).

## Переносы в рубрики `content` и `art`: `output/{MOVES_FILE}`

```json
[
  {{"id": "models-1a2b3c-2026-04-22", "section": "content"}},
  {{"id": "hype-4d5e6f-2026-05-25", "section": "art", "details": "Канны · 13–24 мая"}}
]
```

- Только новости, которые по правилам ниже должны быть в `content` или `art`, а сейчас в другой рубрике
  (рубрика есть во входе, поле `section`). Остальные не упоминай.
- В файлах `output/ГГГГ-ММ-ДД.json` поле `section` не ставь — рубрика меняется только здесь.
- `details` — необязательно, одна строка до 120 символов, только то, что явно есть в тексте новости.
- Тип события у перенесённой новости проверь заново: выставка — `exhibition`, скидка или акция — `promo`.
  Его правь в файле выпуска, как обычно.
- Переносов не нашлось — запиши пустой список `[]`.

## Рубрики

Это тот же текст, по которому рубрика выбирается для новых выпусков.

{RUBRIC_RULES}

## Правила тегирования

Это тот же текст, по которому размечаются новые выпуски. «Справочник в конце промпта» —
это файл `справочник.md`.

{TAGGING_RULES}

## Типы событий

| Код | Название | Когда ставить |
|---|---|---|
{events}

## Как проверять себя

- Тональность сущности отвечает на вопрос «эта новость хорошая или плохая **для этого участника**?»,
  а не «хорошая ли новость в целом». Регулятор, принявший закон, — обычно `neutral`;
  компания, на которую этот закон давит, — `negative`.
- `sentiment` всей новости — общий тон для читателя. Он может не совпадать с тональностью
  отдельных участников, и это нормально.
- Сущностей 1–5. Если хочется больше шести — выбери главных.
- Сомневаешься между `update` и чем-то другим — почти всегда «чем-то другим».

Версия набора: {KIT_VERSION}.
"""


CHECK_PY = r'''#!/usr/bin/env python3
"""Проверка ответов перетегирования. Запуск: python3 check.py [ГГГГ-ММ-ДД ...]"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = json.loads((HERE / "_kit" / "rules.json").read_text(encoding="utf-8"))
SENTIMENTS = set(KIT["sentiments"])
EVENTS = set(KIT["events"])
_SPLIT = re.compile(r"[\s\-_.·'’]+")


def key(name):
    return _SPLIT.sub("", name.casefold().replace("ё", "е"))


CANON = {}
for name, aliases in KIT["entities"].items():
    for v in [name] + aliases:
        CANON[key(v)] = name
STOP = {key(s) for s in KIT["stoplist"]}


def check_file(date):
    errors, warnings = [], []
    src = HERE / "input" / f"{date}.json"
    out = HERE / "output" / f"{date}.json"
    if not out.exists():
        return None, [], []
    inp = json.loads(src.read_text(encoding="utf-8"))
    try:
        ans = json.loads(out.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, [f"невалидный JSON: {e}"], []
    if ans.get("date") != date:
        errors.append(f"date должен быть «{date}»")
    items = ans.get("items")
    if not isinstance(items, list):
        return False, ["нет списка items"], []
    want_ids = [it["id"] for it in inp["items"]]
    got_ids = [it.get("id") for it in items if isinstance(it, dict)]
    missing = [i for i in want_ids if i not in got_ids]
    extra = [i for i in got_ids if i not in want_ids]
    if missing:
        errors.append(f"нет новостей: {', '.join(missing)}")
    if extra:
        errors.append(f"лишние id: {', '.join(map(str, extra))}")
    if not missing and not extra and got_ids != want_ids:
        warnings.append("порядок новостей отличается от входа")
    by_id = {it["id"]: it for it in inp["items"]}

    for it in items:
        if not isinstance(it, dict) or it.get("id") not in by_id:
            continue
        iid, src_it = it["id"], by_id[it["id"]]
        where = f"{iid} ({src_it['headline'][:50]})"

        ents = it.get("entities")
        if not isinstance(ents, list) or not all(isinstance(e, str) and e.strip() for e in ents):
            errors.append(f"{where}: entities — список непустых строк")
            ents = []
        _check_entities(ents, where, errors, warnings)
        if len(ents) > 5:
            warnings.append(f"{where}: {len(ents)} сущностей — оставь 1–5 главных")
        if not ents:
            warnings.append(f"{where}: нет сущностей — точно нет конкретного участника?")
        if len(set(ents)) != len(ents):
            errors.append(f"{where}: повторяющиеся сущности")

        es = it.get("entity_sentiment")
        if not isinstance(es, dict):
            errors.append(f"{where}: entity_sentiment — объект {{сущность: тональность}}")
        else:
            if set(es) != set(ents):
                diff = set(es) ^ set(ents)
                errors.append(f"{where}: ключи entity_sentiment ≠ entities ({', '.join(sorted(diff))})")
            bad = {k: v for k, v in es.items() if v not in SENTIMENTS}
            if bad:
                errors.append(f"{where}: недопустимая тональность в entity_sentiment: {bad}")

        if it.get("sentiment") not in SENTIMENTS:
            errors.append(f"{where}: sentiment = {it.get('sentiment')!r}, нужно positive|negative|neutral")
        if "section" in it:
            errors.append(f"{where}: section в файле выпуска не ставь — переносы только в {KIT['moves_file']}")
        if it.get("event") not in EVENTS:
            errors.append(f"{where}: event = {it.get('event')!r} — нет такого кода")

        rel = it.get("related")
        want_urls = [r["url"] for r in src_it["related"]]
        if not isinstance(rel, list):
            errors.append(f"{where}: related — список (может быть пустым)")
            continue
        got_urls = [r.get("url") for r in rel if isinstance(r, dict)]
        if sorted(map(str, got_urls)) != sorted(want_urls):
            errors.append(f"{where}: related должны быть ровно те же url, что во входе ({len(want_urls)} шт.)")
        for r in rel:
            if not isinstance(r, dict):
                continue
            rw = f"{where} → related {str(r.get('url'))[:60]}"
            re_ = r.get("entities")
            if not isinstance(re_, list) or not all(isinstance(e, str) and e.strip() for e in re_):
                errors.append(f"{rw}: entities — список строк")
            else:
                _check_entities(re_, rw, errors, warnings)
            if r.get("sentiment") not in SENTIMENTS:
                errors.append(f"{rw}: sentiment = {r.get('sentiment')!r}")
    return not errors, errors, warnings


def check_moves():
    """output/ПЕРЕНОСЫ.json: [{id, section, details?}] — переносы в творческие рубрики."""
    path = HERE / "output" / KIT["moves_file"]
    if not path.exists():
        return None, [], []
    try:
        moves = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, [f"невалидный JSON: {e}"], []
    if not isinstance(moves, list):
        return False, ["нужен список объектов {id, section, details?}"], []
    known = {}
    for f in (HERE / "input").glob("*.json"):
        for it in json.loads(f.read_text(encoding="utf-8"))["items"]:
            known[it["id"]] = it
    errors, warnings, seen = [], [], set()
    for m in moves:
        if not isinstance(m, dict):
            errors.append(f"элемент {m!r} — нужен объект")
            continue
        mid, sec = m.get("id"), m.get("section")
        if mid not in known:
            errors.append(f"{mid}: нет такой новости во входе")
            continue
        where = f"{mid} ({known[mid]['headline'][:50]})"
        if mid in seen:
            errors.append(f"{where}: повторяется")
        seen.add(mid)
        if sec not in KIT["creative_sections"]:
            errors.append(f"{where}: section = {sec!r}, можно только {', '.join(KIT['creative_sections'])}")
        elif known[mid]["section"] == sec:
            warnings.append(f"{where}: уже в рубрике {sec} — перенос не нужен")
        det = m.get("details")
        if det is not None and (not isinstance(det, str) or not det.strip()):
            errors.append(f"{where}: details — непустая строка или не указывать")
        elif isinstance(det, str) and len(det) > 120:
            errors.append(f"{where}: details длиннее 120 символов")
        extra = set(m) - {"id", "section", "details"}
        if extra:
            errors.append(f"{where}: лишние поля {', '.join(sorted(extra))}")
    return not errors, errors, warnings


def _check_entities(ents, where, errors, warnings):
    for e in ents:
        k = key(e)
        if k in STOP:
            errors.append(f"{where}: «{e}» — тема из стоп-листа, не сущность")
        elif k in CANON and CANON[k] != e:
            errors.append(f"{where}: «{e}» → пиши «{CANON[k]}» (как в справочнике)")
        elif k not in CANON:
            warnings.append(f"{where}: «{e}» нет в справочнике — ок, если это новая сущность")


def main():
    dates = sys.argv[1:] or sorted(p.stem for p in (HERE / "input").glob("*.json"))
    total = len(list((HERE / "input").glob("*.json")))
    done = n_err = n_warn = 0
    for date in dates:
        if not (HERE / "input" / f"{date}.json").exists():
            print(f"✗ {date}: нет такого входного файла")
            n_err += 1
            continue
        ok, errors, warnings = check_file(date)
        if ok is None:
            if len(sys.argv) > 1:
                print(f"· {date}: ответа ещё нет (output/{date}.json)")
            continue
        done += ok is True
        n_err += len(errors)
        n_warn += len(warnings)
        mark = "✓" if ok else "✗"
        print(f"{mark} {date}: ошибок {len(errors)}, предупреждений {len(warnings)}")
        for e in errors:
            print(f"    ✗ {e}")
        for w in warnings:
            print(f"    ⚠ {w}")
    if len(sys.argv) == 1:
        ok, errors, warnings = check_moves()
        name = KIT["moves_file"]
        if ok is None:
            print(f"· {name}: ещё нет")
        else:
            n_err += len(errors)
            print(f"{'✓' if ok else '✗'} {name}: ошибок {len(errors)}, предупреждений {len(warnings)}")
            for e in errors:
                print(f"    ✗ {e}")
            for w in warnings:
                print(f"    ⚠ {w}")
    print()
    print("Ошибок нет." if n_err == 0 else f"Ошибок: {n_err} — исправь и запусти снова.")
    if len(sys.argv) == 1:
        print(f"Готово {done} из {total} выпусков.")
    sys.exit(1 if n_err else 0)


if __name__ == "__main__":
    main()
'''


def cmd_export(out_dir: Path) -> None:
    if any((out_dir / "output").glob("*.json")):
        sys.exit(f"В {out_dir / 'output'} уже есть ответы — не перезаписываю. Укажи другую папку.")
    (out_dir / "input").mkdir(parents=True, exist_ok=True)
    (out_dir / "output").mkdir(exist_ok=True)
    (out_dir / "_kit").mkdir(exist_ok=True)

    n_files = n_items = n_related = 0
    for path in _issue_paths():
        issue = json.loads(path.read_text(encoding="utf-8"))
        items = [_export_item(it) for it in issue.get("news", []) if it.get("section") != "changelog"]
        if not items:
            continue
        n_files += 1
        n_items += len(items)
        n_related += sum(len(it["related"]) for it in items)
        (out_dir / "input" / path.name).write_text(
            json.dumps({"date": issue.get("date", path.stem), "items": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    d = directory()
    rules = {
        "version":    KIT_VERSION,
        "sentiments": list(SENTIMENTS),
        "events":     list(EVENTS),
        "entities":   {n: m["aliases"] for n, m in d.entities.items()},
        "stoplist":   _stoplist(),
        "creative_sections": list(CREATIVE_SECTIONS),
        "moves_file": MOVES_FILE,
    }
    (out_dir / "_kit" / "rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "check.py").write_text(CHECK_PY, encoding="utf-8")
    (out_dir / "справочник.md").write_text(_reference_md(), encoding="utf-8")
    (out_dir / "ЗАДАНИЕ.md").write_text(_task_md(n_files, n_items, n_related), encoding="utf-8")
    print(f"Набор собран: {out_dir} — {n_files} выпусков, {n_items} новостей, {n_related} связанных заметок")


# ── Применение ────────────────────────────────────────────────────────────────
def _apply_item(item: dict, ans: dict, notes: list[str], date: str) -> None:
    """Переносит теги из ответа в новость через ту же валидацию, что и в пайплайне."""
    tags = {
        "entities":         ans.get("entities") or [],
        "entity_sentiment": ans.get("entity_sentiment") or {},
        "sentiment":        ans.get("sentiment"),
        "event":            ans.get("event"),
    }
    common._fix_entity_tags(item, tags)
    common._fix_event(tags)
    item["tags"] = tags

    by_url = {r.get("url"): r for r in ans.get("related") or [] if isinstance(r, dict)}
    for rel in item.get("related") or []:
        a = by_url.get(rel.get("url"))
        if not a:
            continue
        rel["entities"] = directory().normalize(a.get("entities"))
        rel["sentiment"] = a.get("sentiment") if a.get("sentiment") in SENTIMENTS else "neutral"
    if isinstance(ans.get("note"), str) and ans["note"].strip():
        notes.append(f"{date} · {item['headline'][:70]} — {ans['note'].strip()}")


def cmd_apply(kit_dir: Path, write: bool) -> None:
    out_dir = kit_dir / "output"
    if not out_dir.exists():
        sys.exit(f"Нет папки {out_dir}")

    before_ev, after_ev = Counter(), Counter()
    before_se, after_se = Counter(), Counter()
    diverge = per_entity = 0
    entity_changes = 0
    notes: list[str] = []
    problems: list[str] = []
    updated: dict[Path, dict] = {}
    applied = 0
    directory().unknown.clear()

    for path in _issue_paths():
        ans_path = out_dir / path.name
        if not ans_path.exists():
            problems.append(f"{path.stem}: нет ответа")
            continue
        try:
            ans = json.loads(ans_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append(f"{path.stem}: невалидный JSON ({e})")
            continue
        answers = {a.get("id"): a for a in ans.get("items") or [] if isinstance(a, dict)}
        issue = json.loads(path.read_text(encoding="utf-8"))
        new_issue = copy.deepcopy(issue)
        for item in new_issue.get("news", []):
            if item.get("section") == "changelog":
                continue
            a = answers.pop(item.get("id"), None)
            if a is None:
                problems.append(f"{path.stem}: нет ответа для {item.get('id')}")
                continue
            old = copy.deepcopy(item.get("tags") or {})
            _apply_item(item, a, notes, path.stem)
            applied += 1
            before_ev[old.get("event")] += 1
            after_ev[item["tags"]["event"]] += 1
            before_se[old.get("sentiment")] += 1
            after_se[item["tags"]["sentiment"]] += 1
            if old.get("entities") != item["tags"]["entities"]:
                entity_changes += 1
            for v in item["tags"]["entity_sentiment"].values():
                per_entity += 1
                diverge += v != item["tags"]["sentiment"]
        for extra in answers:
            problems.append(f"{path.stem}: лишний id в ответе {extra}")
        updated[path] = new_issue

    # Переносы в творческие рубрики
    moves_path = out_dir / MOVES_FILE
    moved: list[str] = []
    if moves_path.exists():
        try:
            moves = json.loads(moves_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append(f"{MOVES_FILE}: невалидный JSON ({e})")
            moves = []
        by_id = {it.get("id"): (issue, it) for issue in updated.values() for it in issue.get("news", [])}
        for m in moves if isinstance(moves, list) else []:
            found = by_id.get(m.get("id")) if isinstance(m, dict) else None
            if not found:
                problems.append(f"{MOVES_FILE}: нет новости {m.get('id') if isinstance(m, dict) else m!r}")
                continue
            if m.get("section") not in CREATIVE_SECTIONS:
                problems.append(f"{MOVES_FILE}: {m.get('id')} — недопустимая рубрика {m.get('section')!r}")
                continue
            issue, it = found
            old_section = it.get("section")
            it["section"] = m["section"]
            det = m.get("details")
            if isinstance(det, str) and det.strip():
                it["details"] = det.strip()[:140]
            moved.append(f"{issue.get('date')} · {old_section} → {m['section']} · {it['headline'][:70]}"
                         + (f" · {it['details']}" if it.get("details") else ""))
    else:
        problems.append(f"нет {MOVES_FILE} (если переносов нет — пустой список [])")

    def table(b: Counter, a: Counter, keys) -> str:
        return "\n".join(f"  {k:<12} {b.get(k, 0):>4} → {a.get(k, 0):>4}" for k in keys)

    print(f"Новостей с ответом: {applied}. Выпусков: {len(updated)}.")
    print(f"Сущности изменились у {entity_changes} новостей.")
    print(f"Тональность по сущностям: {per_entity} оценок, {diverge} отличаются от общего тона новости.")
    print("\nТипы событий (было → стало):")
    print(table(before_ev, after_ev, sorted(set(before_ev) | set(after_ev), key=lambda k: -after_ev.get(k, 0))))
    print("\nОбщая тональность (было → стало):")
    print(table(before_se, after_se, ["positive", "negative", "neutral", "rumor"]))
    unknown = directory().unknown
    if unknown:
        print(f"\nНовые сущности (нет в entities.yaml): {len(unknown)}")
        print("  " + ", ".join(f"{n} ×{c}" for n, c in unknown.most_common()))
    if moves_path.exists():
        print(f"\nПереносы в рубрики ({len(moved)}): " + ", ".join(
            f"{s} {n}" for s, n in Counter(x.split(" · ")[1].split(" → ")[1] for x in moved).items()))
        for m in moved:
            print(f"  · {m}")
    if notes:
        print(f"\nЗаметки исполнителя ({len(notes)}):")
        for n in notes:
            print(f"  · {n}")
    if problems:
        print(f"\nПроблемы ({len(problems)}):")
        for p in problems:
            print(f"  ✗ {p}")

    if not write:
        print("\nПробный прогон — файлы не изменены. Для записи: --write")
        return
    if problems:
        sys.exit("\nЕсть проблемы — запись отменена. Исправь ответы и запусти снова.")

    latest = json.loads(LATEST_FILE.read_text(encoding="utf-8")) if LATEST_FILE.exists() else {}
    for path, issue in updated.items():
        text = json.dumps(issue, ensure_ascii=False, indent=2)
        path.write_text(text, encoding="utf-8")
        if latest.get("date") == issue.get("date"):
            LATEST_FILE.write_text(text, encoding="utf-8")

    import importlib.util
    spec = importlib.util.spec_from_file_location("stats", Path(__file__).resolve().parent / "stats.py")
    stats = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stats)
    stats.generate()
    print(f"\nЗаписано выпусков: {len(updated)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="собрать набор для исполнителя")
    e.add_argument("dir", type=Path)
    a = sub.add_parser("apply", help="применить ответы исполнителя")
    a.add_argument("dir", type=Path)
    a.add_argument("--write", action="store_true", help="записать в docs/data")
    args = p.parse_args()
    if args.cmd == "export":
        cmd_export(args.dir)
    else:
        cmd_apply(args.dir, args.write)


if __name__ == "__main__":
    main()
