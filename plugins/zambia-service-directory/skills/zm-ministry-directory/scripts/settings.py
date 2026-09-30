#!/usr/bin/env python3
"""Run profiles: models, parallelism and verification depth, chosen at startup.

Usage:
  settings.py show [--profile fast|balanced|thorough] [--set key=value ...]
  settings.py list

Reads <project>/input/settings.json (falls back to templates/settings.default.json).
Prints the resolved settings as JSON. Exit 2 on an unknown profile, key or bad value.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from zmlog import get_logger, load_json, project_root, templates_dir

log = get_logger("settings")

SCHEMA = {
    "roster_model": str, "researcher_model": str, "verifier_model": str,
    "max_parallel": int, "verifier_chunk": int, "audit_sample": int,
    "reverify_scope": ("pending", "all"),
    "live_check": ("script", "llm"),
    "audit_focus": ("researcher_written", "all"),
}
MODEL_RE = re.compile(r"^(claude-[a-z0-9.-]+|sonnet|opus|haiku|inherit)$")


class SettingsError(Exception):
    pass


def settings_path() -> Path:
    return project_root() / "input" / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    path = path or settings_path()
    if not path.exists():
        path = templates_dir() / "settings.default.json"
    data = load_json(path)
    if "profiles" not in data or not data["profiles"]:
        raise SettingsError(f"{path}: no profiles defined")
    return data


def _cast(key: str, raw):
    rule = SCHEMA.get(key)
    if rule is None:
        raise SettingsError(f"unknown setting {key!r}; valid: {sorted(SCHEMA)}")
    if isinstance(rule, tuple):
        if raw not in rule:
            raise SettingsError(f"{key}={raw!r}: must be one of {list(rule)}")
        return raw
    if rule is int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise SettingsError(f"{key}={raw!r}: must be an integer") from None
        if value < 0 or (key == "max_parallel" and value < 1):
            raise SettingsError(f"{key}={value}: out of range")
        return value
    if not MODEL_RE.match(str(raw)):
        raise SettingsError(f"{key}={raw!r}: not a model id or alias (e.g. claude-sonnet-5-5, sonnet, opus)")
    return str(raw)


def resolve(profile: str | None = None, overrides: dict | None = None, path: Path | None = None) -> dict:
    data = load_settings(path)
    name = profile or data.get("default_profile") or next(iter(data["profiles"]))
    if name not in data["profiles"]:
        raise SettingsError(f"unknown profile {name!r}; available: {list(data['profiles'])}")
    merged = {**data["profiles"][name], **(overrides or {})}
    resolved = {"profile": name}
    for key in SCHEMA:
        if key not in merged:
            raise SettingsError(f"profile {name!r} is missing {key!r}")
        resolved[key] = _cast(key, merged[key])
    resolved["description"] = data["profiles"][name].get("description", "")
    resolved["overrides"] = {k: _cast(k, v) for k, v in (overrides or {}).items()}
    log.debug("resolved profile=%s overrides=%s", name, resolved["overrides"])
    return resolved


def parse_overrides(pairs: list[str]) -> dict:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SettingsError(f"--set expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("show")
    sp.add_argument("--profile")
    sp.add_argument("--set", dest="sets", action="append", default=[])
    sub.add_parser("list")
    args = ap.parse_args()
    try:
        if args.cmd == "list":
            data = load_settings()
            for name, p in data["profiles"].items():
                mark = " (default)" if name == data.get("default_profile") else ""
                print(f"{name}{mark}: {p.get('description', '')}")
            return 0
        print(json.dumps(resolve(args.profile, parse_overrides(args.sets)), indent=2))
        return 0
    except SettingsError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
