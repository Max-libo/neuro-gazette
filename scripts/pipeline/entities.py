#!/usr/bin/env python3
"""
Справочник сущностей Нейрогазеты: нормализация tags.entities по entities.yaml.

Использование в пайплайне:
    from entities import normalize_entities, prompt_reference
    item["tags"]["entities"] = normalize_entities(item["tags"]["entities"])

CLI:
    python scripts/pipeline/entities.py --check    # проверить справочник (коллизии, родители, типы)
    python scripts/pipeline/entities.py --report   # прогнать все выпуски: что склеится, чего нет в справочнике
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

REPO_ROOT     = Path(__file__).resolve().parent.parent.parent
ENTITIES_FILE = REPO_ROOT / "entities.yaml"
DATA_DIR      = REPO_ROOT / "docs" / "data"

VALID_TYPES = {"company", "model", "product", "person", "gov", "place", "org", "law", "tech"}

# Порядок групп в справочнике для промпта
_PROMPT_GROUPS = [
    ("company", "Компании"),
    ("model",   "Модели"),
    ("product", "Продукты"),
    ("person",  "Люди"),
    ("gov",     "Госорганы"),
    ("place",   "Страны и регионы"),
    ("org",     "Организации, медиа, вузы"),
    ("law",     "Законы"),
    ("tech",    "Технологии"),
]

_SPLIT_CHARS = re.compile(r"[\s\-_.·'’]+")


def entity_key(name: str) -> str:
    """Ключ сравнения: регистр, ё/е, пробелы, дефисы, точки, апострофы не важны."""
    return _SPLIT_CHARS.sub("", name.casefold().replace("ё", "е"))


# ── Загрузка ──────────────────────────────────────────────────────────────────
class _StrictLoader(yaml.SafeLoader):
    """SafeLoader, который падает на дублирующихся ключах (PyYAML по умолчанию молча берёт последний)."""


def _construct_mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ValueError(f"entities.yaml: дублирующийся ключ «{key}» (строка {key_node.start_mark.line + 1})")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


class EntityDirectory:
    def __init__(self, path: Path = ENTITIES_FILE):
        self.path = path
        self.entities: dict[str, dict] = {}   # каноническое имя → {type, parent, aliases}
        self.index: dict[str, str] = {}       # ключ → каноническое имя
        self.stop: set[str] = set()           # ключи стоп-листа
        self.errors: list[str] = []
        self.unknown: Counter = Counter()     # неизвестные сущности, встреченные при нормализации
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self.errors.append(f"нет файла {self.path}")
            return
        raw = yaml.load(self.path.read_text(encoding="utf-8"), Loader=_StrictLoader) or {}

        for name, meta in (raw.get("entities") or {}).items():
            name = str(name).strip()
            meta = meta or {}
            aliases = [str(a).strip() for a in (meta.get("aliases") or []) if str(a).strip()]
            self.entities[name] = {
                "type":    meta.get("type"),
                "parent":  meta.get("parent"),
                "aliases": aliases,
            }
            for variant in [name, *aliases]:
                k = entity_key(variant)
                owner = self.index.get(k)
                if owner and owner != name:
                    self.errors.append(f"«{variant}» ({name}) совпадает с «{owner}» — склеятся неоднозначно")
                    continue
                self.index[k] = name

        for word in raw.get("stoplist") or []:
            k = entity_key(str(word))
            if k in self.index:
                self.errors.append(f"стоп-слово «{word}» совпадает с сущностью «{self.index[k]}»")
            self.stop.add(k)

        for name, meta in self.entities.items():
            if meta["type"] not in VALID_TYPES:
                self.errors.append(f"{name}: неизвестный type «{meta['type']}»")
            parent = meta["parent"]
            if parent and parent not in self.entities:
                self.errors.append(f"{name}: parent «{parent}» нет в справочнике")
            if meta["type"] == "person" and parent:
                self.errors.append(f"{name}: у людей parent не ставим")
            # цикл в родителях
            seen, cur = {name}, parent
            while cur and cur in self.entities:
                if cur in seen:
                    self.errors.append(f"{name}: цикл в parent")
                    break
                seen.add(cur)
                cur = self.entities[cur]["parent"]

    # ── API ──
    def canonical(self, name: str) -> str | None:
        """Каноническое имя, '' для стоп-слова, None если сущности нет в справочнике."""
        k = entity_key(name)
        if not k or k in self.stop:
            return ""
        return self.index.get(k)

    def normalize(self, names) -> list[str]:
        """Приводит список к каноническим именам, убирает стоп-слова и дубли, сохраняет порядок."""
        out: list[str] = []
        for raw in names or []:
            if not isinstance(raw, str):
                continue
            name = " ".join(raw.split())
            if not name:
                continue
            canon = self.canonical(name)
            if canon == "":
                continue
            if canon is None:
                self.unknown[name] += 1
                canon = name
            if canon not in out:
                out.append(canon)
        return out

    def type_of(self, name: str) -> str | None:
        meta = self.entities.get(name)
        return meta["type"] if meta else None

    def ancestors(self, name: str) -> list[str]:
        """Цепочка родителей: Claude Code → [Anthropic]; Claude Opus 4.7 → [Claude, Anthropic]."""
        chain, cur = [], (self.entities.get(name) or {}).get("parent")
        while cur and cur not in chain:
            chain.append(cur)
            cur = (self.entities.get(cur) or {}).get("parent")
        return chain

    def prompt_reference(self) -> str:
        """Список канонических имён по группам — для системного промпта этапа 4."""
        lines = []
        for t, title in _PROMPT_GROUPS:
            names = sorted((n for n, m in self.entities.items() if m["type"] == t), key=str.casefold)
            if names:
                lines.append(f"{title}: {', '.join(names)}")
        return "\n".join(lines)


_DIRECTORY: EntityDirectory | None = None


def directory() -> EntityDirectory:
    global _DIRECTORY
    if _DIRECTORY is None:
        _DIRECTORY = EntityDirectory()
    return _DIRECTORY


def normalize_entities(names) -> list[str]:
    return directory().normalize(names)


def prompt_reference() -> str:
    return directory().prompt_reference()


# ── CLI ───────────────────────────────────────────────────────────────────────
def _iter_issue_entities():
    for path in sorted(DATA_DIR.glob("????-??-??.json")):
        issue = json.loads(path.read_text(encoding="utf-8"))
        for item in issue.get("news", []):
            yield (item.get("tags") or {}).get("entities") or []
            for rel in item.get("related") or []:
                yield rel.get("entities") or []


def _cmd_check(d: EntityDirectory) -> int:
    if d.errors:
        print(f"Справочник: {len(d.errors)} ошибок")
        for e in d.errors:
            print(f"  ✗ {e}")
        return 1
    types = Counter(m["type"] for m in d.entities.values())
    print(f"Справочник в порядке: {len(d.entities)} сущностей, {len(d.index)} вариантов написания, "
          f"{len(d.stop)} стоп-слов")
    print("  " + ", ".join(f"{t}: {n}" for t, n in types.most_common()))
    return 0


def _cmd_report(d: EntityDirectory, limit: int) -> int:
    before, after, merged, dropped = Counter(), Counter(), Counter(), Counter()
    for names in _iter_issue_entities():
        before.update(names)
        after.update(d.normalize(names))
        for n in names:
            canon = d.canonical(n)
            if canon == "":
                dropped[n] += 1
            elif canon and canon != n:
                merged[(n, canon)] += 1

    covered = sum(c for n, c in after.items() if n in d.entities)
    total = sum(after.values())
    print(f"Уникальных сущностей: {len(before)} → {len(after)}")
    print(f"Упоминаний в справочнике: {covered}/{total} ({covered / total:.0%})" if total else "Нет данных")

    print(f"\nСклеено ({len(merged)} вариантов):")
    for (src, dst), c in sorted(merged.items(), key=lambda x: -x[1]):
        print(f"  {src} → {dst}  ×{c}")

    print(f"\nУбрано стоп-листом ({sum(dropped.values())}):")
    print("  " + ", ".join(f"{n} ×{c}" for n, c in dropped.most_common()))

    unknown = Counter({n: c for n, c in after.items() if n not in d.entities})
    print(f"\nНет в справочнике: {len(unknown)} (повторяются ≥2 раз: {sum(1 for c in unknown.values() if c >= 2)})")
    for n, c in unknown.most_common(limit):
        print(f"  {n} ×{c}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="проверить entities.yaml")
    p.add_argument("--report", action="store_true", help="отчёт по всем выпускам")
    p.add_argument("--limit", type=int, default=60, help="сколько неизвестных сущностей показать")
    args = p.parse_args()

    d = EntityDirectory()
    code = _cmd_check(d)
    if args.report:
        print()
        _cmd_report(d, args.limit)
    sys.exit(code)


if __name__ == "__main__":
    main()
