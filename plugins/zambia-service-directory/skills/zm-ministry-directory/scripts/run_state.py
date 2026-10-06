#!/usr/bin/env python3
"""Run folder and progress ledger for one ministry run (supports --resume).

Usage:
  run_state.py init --ministry "<Name>" [--import X.xlsx] [--include-local] [--ministry-no N]
                    [--profile fast|balanced|thorough] [--set key=value ...] [--no-dotgov]
  run_state.py set <RUN> <slug> <state> [--note "..."]
  run_state.py show <RUN>
  run_state.py pending <RUN>            slugs not yet verified/done/excluded (one per line)

States: pending -> researched -> repair -> verified | unresolved -> done ; excluded.
init prints JSON {run, ministry, ministry_no, previous_state, existing_agencies}.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import zmcontract as c
import dotgov_registry
import ministries as reg
import settings as cfg
from eservices import load_catalogue
from zmlog import dump_json, get_logger, load_json, project_root
from zmworkbook import read_workbook

log = get_logger("run_state")
STATES = ["pending", "researched", "repair", "verified", "unresolved", "done", "excluded"]
FINISHED = {"verified", "done", "excluded", "unresolved"}


def ministry_dir(ministry: str) -> Path:
    return project_root() / "output" / c.file_safe_name(ministry)


IS_WINDOWS = os.name == "nt"
WINDOWS_MAX_PATH = 260
RUN_PATH_BUDGET = 130  # longest file inside a run: evidence/<agency>/sources/<sha8>-<slug40>/meta.json


def windows_long_paths_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def path_length_warning(run: Path) -> str | None:
    """Windows refuses paths over 260 characters unless long paths are switched on."""
    if not IS_WINDOWS or len(str(run)) + RUN_PATH_BUDGET <= WINDOWS_MAX_PATH or windows_long_paths_enabled():
        return None
    return (f"project folder path is long ({len(str(run))} characters to the run folder); files may exceed the "
            f"Windows {WINDOWS_MAX_PATH}-character limit. Move the project to a short folder such as C:\\zm\\ "
            "or turn on Windows long paths (see docs/USER-GUIDE.md, Windows).")


def init(ministry: str, import_path: Path | None, include_local: bool, ministry_no: int | None,
         profile: str | None = None, overrides: dict | None = None, dotgov: bool = True) -> dict:
    resolved = cfg.resolve(profile, overrides)  # fail early on a bad profile or setting
    entry = reg.lookup(ministry, reg.default_registry())
    name = entry["name"]
    if ministry_no is not None and ministry_no != entry["ministry_no"]:
        log.warning("--ministry-no %d overrides registry value %d", ministry_no, entry["ministry_no"])
    number = ministry_no if ministry_no is not None else entry["ministry_no"]
    mdir = ministry_dir(name)
    run = mdir / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    for sub in ("agencies", "eservices", "verify", "evidence", "logs", "existing"):
        (run / sub).mkdir(parents=True, exist_ok=True)
    current = mdir / c.workbook_filename(name)
    previous = current if current.exists() else (import_path.resolve() if import_path else None)
    existing = []
    if previous:
        wb = read_workbook(previous)
        counts = {s["agency"]: len(s["rows"]) for s in wb["sections"]}
        existing = [{"name": a.get("Entity"), "entity_type": a.get("Entity Type"), "rows": counts.get(a.get("Entity"), 0),
                     "notes": a.get("Notes"), "source_link": a.get("Source Link")} for a in wb["agencies"]]
    catalogue = load_catalogue()
    registry = dotgov_registry.load() if dotgov else None
    dotgov_meta = ({"enabled": True, "path": registry["_path"], "sha256": registry["source"]["sha256"],
                    "services": len(registry["services"])} if registry else {"enabled": False})
    meta = {"ministry": name, "ministry_input": ministry, "ministry_no": number, "ministry_no_assigned": entry["assigned"],
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "previous_state": str(previous) if previous else None, "include_local": include_local,
            "catalogue_fetched_at": catalogue.get("fetched_at"), "workbook": str(current),
            "settings": resolved, "dotgov": dotgov_meta}
    dump_json(meta, run / "run.json")
    dump_json({"agencies": {}}, run / "progress.json")
    log.info("run initialised %s ministry=%s no=%d previous=%s profile=%s dotgov=%s", run, name, number, previous,
             resolved["profile"], dotgov_meta.get("services", "off"))
    out = {"run": str(run), **meta, "existing_agencies": existing}
    warning = path_length_warning(run)
    if warning:
        log.warning(warning)
        out["warnings"] = [warning]
    return out


def set_state(run: Path, slug: str, state: str, note: str | None) -> dict:
    if state not in STATES:
        raise ValueError(f"unknown state {state!r}; use one of {STATES}")
    path = run / "progress.json"
    ledger = load_json(path)
    entry = ledger["agencies"].setdefault(slug, {"history": []})
    entry["state"] = state
    entry["history"].append({"state": state, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "note": note})
    dump_json(ledger, path)
    log.info("ledger %s -> %s%s", slug, state, f" ({note})" if note else "")
    return entry


def run_settings(run: Path) -> dict:
    """Settings saved in RUN/run.json. A run started before `agent_models` existed gets them filled in
    (and saved) from its saved `*_model` values, so `--resume` passes short model names too."""
    path = run / "run.json"
    meta = load_json(path)
    saved = meta.get("settings") or {}
    if not saved:
        raise ValueError(f"{path}: no settings saved; start a new run")
    if "agent_models" not in saved:
        saved["agent_models"] = {role: cfg.agent_alias(saved[f"{role}_model"]) for role in cfg.AGENT_ROLES}
        meta["settings"] = saved
        dump_json(meta, path)
        log.info("run.json had no agent_models; added %s", saved["agent_models"])
    return saved


def pending(run: Path) -> list[str]:
    ledger = load_json(run / "progress.json")["agencies"]
    roster_path = run / "roster.json"
    slugs = [a["slug"] for a in load_json(roster_path)["agencies"]] if roster_path.exists() else list(ledger)
    return [s for s in slugs if ledger.get(s, {}).get("state") not in FINISHED]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("init")
    ip.add_argument("--ministry", required=True)
    ip.add_argument("--import", dest="import_path", type=Path)
    ip.add_argument("--include-local", action="store_true")
    ip.add_argument("--ministry-no", type=int)
    ip.add_argument("--profile", help="fast | balanced | thorough (default from input/settings.json)")
    ip.add_argument("--set", dest="sets", action="append", default=[], help="override one setting: key=value")
    ip.add_argument("--no-dotgov", action="store_true", help="research DotGov services too (no placeholders)")
    sp = sub.add_parser("set")
    sp.add_argument("run", type=Path)
    sp.add_argument("slug")
    sp.add_argument("state")
    sp.add_argument("--note")
    for name in ("show", "pending", "settings"):
        p = sub.add_parser(name)
        p.add_argument("run", type=Path)
    args = ap.parse_args()
    try:
        if args.cmd == "init":
            print(json.dumps(init(args.ministry, args.import_path, args.include_local, args.ministry_no,
                                  args.profile, cfg.parse_overrides(args.sets), dotgov=not args.no_dotgov),
                             ensure_ascii=False, indent=2))
        elif args.cmd == "set":
            print(json.dumps(set_state(args.run, args.slug, args.state, args.note), ensure_ascii=False))
        elif args.cmd == "show":
            print(json.dumps(load_json(args.run / "progress.json"), ensure_ascii=False, indent=2))
        elif args.cmd == "settings":
            print(json.dumps(run_settings(args.run), ensure_ascii=False, indent=2))
        elif args.cmd == "pending":
            print("\n".join(pending(args.run)))
    except (reg.RegistryError, cfg.SettingsError, ValueError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
