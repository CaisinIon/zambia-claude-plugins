#!/usr/bin/env python3
"""Build the next version of a ministry workbook from verified agency files.

Usage:
  build_workbook.py --ministry "Ministry of Tourism" --agencies <run>/agencies/*.json
                    [--out output/<Ministry_Name>] [--spec templates/workbook_spec.json]
                    [--import existing.xlsx] [--roster <run>/roster.json] [--ministry-no N]
                    [--allow-errors] [--report <run>/build.json]

Previous state = the current workbook in --out (it is first copied to versions/),
otherwise --import. Only passports with verification Verified / Verified with
limitations / DotGov placeholder are written. A previous row is removed only by an explicit
action=remove passport; other previous rows that no agency file mentions are
kept and reported as 'carried_over'.

Prints a JSON summary: added, corrected, removed, unchanged, carried_over,
ministry_total, per-agency counts, workbook and version paths, changes.
Exit 0 ok, 1 validation errors (unless --allow-errors), 2 bad input.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import dotgov_registry
import zmcontract as c
import ministries as reg
from validate_passports import validate
from zmlog import dump_json, get_logger, load_json, project_root, templates_dir
from zmworkbook import eservices_id_from_link, read_workbook, write_workbook

log = get_logger("build_workbook")


def _norm(text) -> str:
    return c.normalise_name(str(text or ""))


def normalise_value(value) -> str:
    text = str(value or "").strip()
    return c.DEPRECATED_PLACEHOLDERS.get(text, text)


def passport_row(p: dict) -> dict:
    return {f: normalise_value(p.get(f)) for f in c.PASSPORT_FIELDS}


def match_previous(prev_rows: list[dict], p: dict, used: set[int], aliases: tuple = ()) -> dict | None:
    """Find the previous row for passport p: eServices ID, then agency+name, then name."""
    sid = p.get("eservices_id") or eservices_id_from_link(p.get("source_link"))
    candidates = [r for r in prev_rows if r["row"] not in used]
    if sid:
        for r in candidates:
            if eservices_id_from_link(r.get("source_link")) == sid:
                return r
    former = _norm(p.get("previous_name"))
    if former:  # the researcher renamed an imported service; follow it back to its old row
        for r in candidates:
            if _norm(r.get("service_name")) == former:
                return r
    name = _norm(p.get("service_name"))
    agency = c.agency_key(p.get("agency"))
    for r in candidates:
        if _norm(r.get("service_name")) == name and (c.agency_key(r.get("agency")) == agency
                                                    or r.get("agency") in aliases):
            return r
    for r in candidates:
        if _norm(r.get("service_name")) == name and not eservices_id_from_link(r.get("source_link")):
            return r
    return None


def dotgov_suffix(passports: list[dict]) -> str:
    n = sum(1 for p in passports if dotgov_registry.is_placeholder(p))
    return c.NOTES_DOTGOV_SUFFIX.format(dotgov=n) if n else ""


def notes_for(agency: dict, rows: list[dict], passports: list[dict]) -> str:
    if agency.get("notes"):
        return agency["notes"]
    return _notes_core(agency, rows, passports) + dotgov_suffix(passports)


def _notes_core(agency: dict, rows: list[dict], passports: list[dict]) -> str:
    total = len(rows)
    es = [p for p in passports if p.get("origin") == "eservices"]
    moved_in = [p for p in es if p.get("reassigned_from")]
    other = total - len(es)
    label = agency.get("other_sources_summary") or "other official sources"
    if es and len(moved_in) == len(es) and other == 0:
        providers = "; ".join(sorted({p["reassigned_from"] for p in moved_in}))
        return c.NOTES_LISTED_UNDER.format(total=total, provider=providers)
    if es and other:
        return c.NOTES_LISTED.format(total=total, eservices=len(es), other=other, other_source=label)
    if es:
        return c.NOTES_LISTED_ONLY.format(total=total)
    if total:
        return c.NOTES_NOT_LISTED.format(total=total, other_source=label)
    return c.NOTES_NONE.format(reason=agency.get("status_reason") or "no public services found in official sources")


def next_version_path(current: Path) -> Path:
    versions = current.parent / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for f in versions.glob(f"{current.stem}_v*.xlsx")
            if (m := re.search(r"_v(\d{3})_", f.name))]
    return versions / f"{current.stem}_v{(max(nums) if nums else 0) + 1:03d}_{date.today().isoformat()}.xlsx"


def build(ministry: str, agencies: list[dict], out_dir: Path, spec: dict, import_path: Path | None,
          roster: dict | None, ministry_no: int | None) -> dict:
    target = out_dir / c.workbook_filename(ministry)
    prev_path = target if target.exists() else import_path
    prev = read_workbook(prev_path) if prev_path else {"passports": [], "agencies": [], "scope": None}
    log.info("previous state=%s rows=%d", prev_path, len(prev["passports"]))

    if ministry_no is None:
        ministry_no = reg.lookup(ministry, reg.default_registry())["ministry_no"]

    slugs = [a.get("slug") for a in (roster or {}).get("agencies", [])]
    names = [a["official_name"] for a in (roster or {}).get("agencies", [])]

    def position(a: dict) -> int:  # roster order; slug survives a rename by the researcher
        if a.get("slug") in slugs:
            return slugs.index(a["slug"])
        return names.index(a["official_name"]) if a["official_name"] in names else 999
    agencies = sorted(agencies, key=position)

    used: set[int] = set()
    changes, counts = [], {"added": 0, "corrected": 0, "removed": 0, "unchanged": 0, "carried_over": 0, "unresolved": 0,
                           "dotgov_placeholders": 0}
    dotgov_per_agency: dict[str, int] = {}
    sections, agency_rows, per_agency = [], [], {}

    for a in agencies:
        rows, written = [], []
        for p in a.get("passports", []):
            prev_row = match_previous(prev["passports"], p, used, tuple(a.get("aliases") or []) + (a.get("roster_name") or "",))
            if prev_row:
                used.add(prev_row["row"])
            if p.get("action") == "remove":
                if prev_row:
                    counts["removed"] += 1
                    changes.append({"change": "removed", "agency": a["official_name"], "service": p.get("service_name"),
                                    "reason": p.get("action_reason"), "previous_row": prev_row["row"]})
                continue
            if p.get("verification") not in c.WRITABLE_VERIFICATION:
                counts["unresolved"] += 1
                changes.append({"change": "unresolved", "agency": a["official_name"], "service": p.get("service_name"),
                                "reason": p.get("verification_notes")})
                if prev_row:
                    used.discard(prev_row["row"])  # keep the old row as carried over
                continue
            row = passport_row(p)
            if prev_row:
                diff = [f for f in c.PASSPORT_FIELDS if normalise_value(prev_row.get(f)) != row[f]
                        or str(prev_row.get(f) or "").strip() != row[f]]
                kind = "corrected" if diff else "unchanged"
                changes.append({"change": kind, "agency": a["official_name"], "service": row["service_name"],
                                "fields": diff, "previous_row": prev_row["row"]})
            else:
                kind = "added"
                changes.append({"change": "added", "agency": a["official_name"], "service": row["service_name"]})
            counts[kind] += 1
            if dotgov_registry.is_placeholder(p):
                changes[-1]["dotgov"] = True
                counts["dotgov_placeholders"] += 1
                dotgov_per_agency[a["official_name"]] = dotgov_per_agency.get(a["official_name"], 0) + 1
            rows.append(row)
            written.append(p)
        per_agency[a["official_name"]] = len(rows)
        status = a["status"]
        if status == "Included" and not rows and not any(
                c.agency_key(r.get("agency") or r.get("section")) == c.agency_key(a["official_name"])
                for r in prev["passports"] if r["row"] not in used):
            status = "Excluded"
            a.setdefault("status_reason", None)
            a["status_reason"] = a["status_reason"] or "no public-facing service confirmed in official sources"
            changes.append({"change": "agency_excluded", "agency": a["official_name"], "reason": a["status_reason"]})
            log.info("agency %s has no verified passports -> Excluded", a["official_name"])
        if status == "Included":
            sections.append({"agency": a["official_name"], "rows": rows})
        agency_rows.append({
            "official_name": a["official_name"], "entity_type": a["entity_type"],
            "digitizable_service_areas": a.get("digitizable_service_areas"), "status": status,
            "notes": notes_for(a, rows, written), "source_link": a.get("source_link"),
        })

    # previous rows that no agency file mentions are kept (not confirmed for removal)
    # a previous section maps to the agency's current name via its roster name or aliases, so a rename by the researcher is followed
    current = {}
    for a in agencies:
        for n in (a["official_name"], a.get("roster_name"), *(a.get("aliases") or [])):
            if n:
                current[c.agency_key(n)] = a["official_name"]
    for r in prev["passports"]:
        if r["row"] in used:
            continue
        counts["carried_over"] += 1
        old_name = r.get("agency") or r.get("section")
        agency_name = current.get(c.agency_key(old_name), old_name)  # follow a rename, e.g. 'X' -> 'X (ACR)'
        changes.append({"change": "carried_over", "agency": agency_name, "service": r.get("service_name"),
                        "previous_row": r["row"]})
        section = next((s for s in sections if s["agency"] == agency_name), None)
        if section is None:
            section = {"agency": agency_name, "rows": []}
            sections.append(section)
        # not re-checked in this run: keep the original text (no placeholder rewrite)
        row = {f: r.get(f) for f in c.PASSPORT_FIELDS}
        row["agency"] = agency_name
        section["rows"].append(row)
    for row_a in agency_rows:
        section = next((s for s in sections if s["agency"] == row_a["official_name"]), None)
        src = next((a for a in agencies if a["official_name"] == row_a["official_name"]), None)
        if section and src and not src.get("notes") and len(section["rows"]) != per_agency.get(row_a["official_name"], 0):
            carried = len(section["rows"]) - per_agency[row_a["official_name"]]
            written_es = [p for p in src.get("passports", []) if p.get("origin") == "eservices"
                          and p.get("verification") in c.WRITABLE_VERIFICATION and p.get("action") != "remove"]
            label = src.get("other_sources_summary") or "other official sources"
            total = len(section["rows"])
            row_a["notes"] = (c.NOTES_LISTED.format(total=total, eservices=len(written_es), other=total - len(written_es), other_source=label)
                              if written_es else c.NOTES_NOT_LISTED.format(total=total, other_source=label))
            row_a["notes"] += dotgov_suffix(src.get("passports", []))
            row_a["notes"] += f"; {carried} kept from the previous workbook without re-check"
    known = {s["agency"] for s in sections}
    for pa in prev.get("agencies", []):
        if pa.get("Entity") and c.agency_key(pa["Entity"]) not in current:
            agency_rows.append({"official_name": pa["Entity"], "entity_type": pa.get("Entity Type"),
                                "digitizable_service_areas": pa.get("Digitizable Service Areas"),
                                "status": pa.get("Status") or "Included", "notes": pa.get("Notes"),
                                "source_link": pa.get("Source Link")})
            if pa["Entity"] not in known:
                log.warning("agency %r carried over from previous workbook (not in this run)", pa["Entity"])

    version_path = None
    if target.exists():
        version_path = next_version_path(target)
        shutil.copy2(target, version_path)
        log.info("previous workbook archived to %s", version_path)

    data = {"ministry": ministry, "ministry_no": ministry_no, "scope": prev.get("scope"),
            "agencies": agency_rows, "sections": sections}
    write_workbook(target, spec, data)
    total = sum(len(s["rows"]) for s in sections)
    summary = {**counts, "ministry_total": total, "ministry_no": ministry_no,
               "agencies_included": sum(1 for a in agency_rows if a["status"] == "Included"),
               "per_agency": {s["agency"]: len(s["rows"]) for s in sections},
               "dotgov_per_agency": dotgov_per_agency,
               "workbook": str(target), "previous_version": str(version_path) if version_path else None,
               "previous_state": str(prev_path) if prev_path else None, "changes": changes}
    log.info("build done added=%d corrected=%d removed=%d unchanged=%d carried_over=%d unresolved=%d "
             "dotgov_placeholders=%d total=%d", counts["added"], counts["corrected"], counts["removed"],
             counts["unchanged"], counts["carried_over"], counts["unresolved"], counts["dotgov_placeholders"], total)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ministry", required=True)
    ap.add_argument("--agencies", nargs="+", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--spec", type=Path, default=templates_dir() / "workbook_spec.json")
    ap.add_argument("--import", dest="import_path", type=Path)
    ap.add_argument("--roster", type=Path)
    ap.add_argument("--ministry-no", type=int)
    ap.add_argument("--allow-errors", action="store_true")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    try:
        agencies = [load_json(p) for p in args.agencies]
        spec = load_json(args.spec)
        roster = load_json(args.roster) if args.roster else None
    except (OSError, json.JSONDecodeError) as exc:
        log.error("bad input: %s", exc)
        return 2
    # the run folder is the parent of agencies/: honour its --no-dotgov choice
    report = validate(agencies, dotgov_registry.for_run_dir(args.agencies[0].resolve().parent.parent))
    if not report["ok"] and not args.allow_errors:
        log.error("validation failed with %d errors; fix agency files or pass --allow-errors", report["errors"])
        print(json.dumps({"ok": False, "validation": report}, ensure_ascii=False, indent=2))
        return 1
    out = args.out or project_root() / "output" / c.file_safe_name(args.ministry)
    try:
        summary = build(args.ministry, agencies, out, spec, args.import_path, roster, args.ministry_no)
    except PermissionError as exc:  # Windows locks a workbook that is open in Excel
        log.error("cannot write %s: %s. Close the workbook in Excel (or any app using it) and run this step again.",
                  exc.filename or out, exc.strerror or exc)
        return 3
    summary["validation"] = {"errors": report["errors"], "warnings": report["warnings"]}
    if args.report:
        dump_json(summary, args.report)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
