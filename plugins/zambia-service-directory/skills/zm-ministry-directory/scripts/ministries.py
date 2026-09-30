#!/usr/bin/env python3
"""Ministry registry: fixed Ministry No. per ministry.

Usage:
  ministries.py lookup "<Ministry Name>" [--registry input/ministries.json] [--no-assign]
  ministries.py check [--registry ...]

lookup prints JSON {ministry_no, name, acronym, website, assigned}. When the
ministry is not in the registry, it assigns max(ministry_no)+1, saves the file,
and sets assigned=true (unless --no-assign, then exit 3).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rapidfuzz import fuzz

from zmlog import dump_json, get_logger, load_json, project_root, templates_dir
from zmschema import schema_errors

log = get_logger("ministries")
MATCH_THRESHOLD = 90


class RegistryError(Exception):
    """Registry file is invalid (schema, duplicate numbers or names)."""


def default_registry() -> Path:
    return project_root() / "input" / "ministries.json"


def _validated(data: list[dict], path: Path) -> list[dict]:
    errors = schema_errors(data, "ministries")
    if errors:
        raise RegistryError("ERROR [ministry-no] invalid registry: " + "; ".join(errors))
    numbers = [m["ministry_no"] for m in data]
    names = [m["name"].strip().lower() for m in data]
    dup_no = sorted({n for n in numbers if numbers.count(n) > 1})
    dup_name = sorted({n for n in names if names.count(n) > 1})
    if dup_no or dup_name:
        raise RegistryError(f"ERROR [ministry-no] duplicates: numbers={dup_no} names={dup_name}")
    log.debug("registry loaded path=%s entries=%d", path, len(data))
    return data


def load_registry(path: Path) -> list[dict]:
    if not path.exists():
        # Only the default project registry seeds from the shipped template —
        # an explicit --registry path (or a test's tmp_path) starts empty, as before.
        if path == default_registry():
            template = templates_dir() / "ministries.default.json"
            if template.is_file():
                log.info("registry not found at %s; seeded from template %s", path, template)
                return _validated(load_json(template), path)
        log.warning("registry not found at %s; starting empty", path)
        return []
    return _validated(load_json(path), path)


def find(registry: list[dict], name: str) -> dict | None:
    """Exact (case-insensitive) name/acronym match, then fuzzy name match >= threshold."""
    key = name.strip().lower()
    for entry in registry:
        if entry["name"].strip().lower() == key or (entry.get("acronym") or "").lower() == key:
            return entry
    best, best_score = None, 0.0
    for entry in registry:
        score = fuzz.token_sort_ratio(key, entry["name"].lower())
        if score > best_score:
            best, best_score = entry, score
    if best and best_score >= MATCH_THRESHOLD:
        log.info("fuzzy match %r -> %r score=%.1f", name, best["name"], best_score)
        return best
    return None


def lookup(name: str, path: Path, assign: bool = True) -> dict:
    registry = load_registry(path)
    entry = find(registry, name)
    if entry:
        return {**entry, "assigned": False}
    if not assign:
        raise KeyError(name)
    next_no = max((m["ministry_no"] for m in registry), default=0) + 1
    entry = {"ministry_no": next_no, "name": name.strip(), "acronym": None, "website": None}
    registry.append(entry)
    dump_json(registry, path)
    log.info("[ministry-no] assigned %d to %s (saved %s)", next_no, name, path)
    return {**entry, "assigned": True}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("lookup")
    lp.add_argument("name")
    lp.add_argument("--registry", type=Path, default=None)
    lp.add_argument("--no-assign", action="store_true")
    cp = sub.add_parser("check")
    cp.add_argument("--registry", type=Path, default=None)
    args = ap.parse_args()
    path = args.registry or default_registry()
    try:
        if args.cmd == "check":
            print(json.dumps({"ok": True, "entries": len(load_registry(path))}))
            return 0
        print(json.dumps(lookup(args.name, path, assign=not args.no_assign), ensure_ascii=False))
        return 0
    except RegistryError as exc:
        log.error("%s", exc)
        return 2
    except KeyError:
        log.error("ministry %r not in registry and --no-assign set", args.name)
        return 3


if __name__ == "__main__":
    sys.exit(main())
