#!/usr/bin/env python3
"""Audit helper for /zm-verify-ministry.

Usage:
  audit.py prepare <workbook.xlsx> --out OUT [--run RUN] [--sample N] [--seed S] [--focus researcher_written|all] [--chunk N]
  audit.py finish --out OUT

prepare: rebuilds agency data (from RUN/agencies when given, else from the sheet),
runs the passport rules, checks eServices drift against today's catalogue, and
writes the re-verification sample to OUT/audit/agencies/<slug>.json (verifier input)
plus OUT/audit_prepare.json. With --run the slugs are the run's own (the file names in RUN/agencies).
An agency with more sampled rows than --chunk (default 6, 0 = never split) is listed under `parts` as
{part, indices}: start one verifier per part (PART, INDICES); finish merges the part files.
finish: combines OUT/workbook_audit.json, OUT/audit_prepare.json and
OUT/audit/verify/*.json into OUT/audit.json and OUT/audit.md (PASS/FAIL). A verdict that covers fewer
rows than were sampled (e.g. a verifier that hit its turn limit) counts as a missing verdict.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import dotgov_registry
import zmcontract as c
from eservices import agency_services, find_agency, load_catalogue
from validate_passports import validate
from zmlog import dump_json, get_logger, load_json
from zmworkbook import eservices_id_from_link, read_workbook

log = get_logger("audit")
# Without saved evidence these rules can't be judged from the sheet alone; the verifier checks sources live.
SHEET_ONLY_SKIP = {"R-SOURCE", "R-PENDING", "R-DRAFT", "R-COUNT", "R-REASSIGN", "R-SCHEMA"}
PROVIDER_MATCH = 85
AUDIT_CHUNK = 6
PART_FILE = re.compile(r"\.part\d+\.json$")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower())[:40].strip("-")


def split_parts(count: int, chunk: int) -> list[dict]:
    """Even parts of at most `chunk` passports as {part, indices} (indices like '0-4'); [] when no split is needed."""
    if chunk <= 0 or count <= chunk:
        return []
    k = -(-count // chunk)
    size = -(-count // k)
    parts = []
    for n in range(k):
        lo, hi = n * size, min(count, (n + 1) * size) - 1
        if lo <= hi:
            parts.append({"part": n + 1, "indices": f"{lo}-{hi}" if hi > lo else str(lo)})
    return parts


def agencies_from_sheet(data: dict) -> list[dict]:
    ministry = data.get("ministry")
    out = []
    for a in data["agencies"]:
        name = a.get("Entity")
        rows = [p for p in data["passports"] if p.get("section") == name]
        passports = []
        for r in rows:
            sid = eservices_id_from_link(r.get("source_link"))
            p = {**{f: (r.get(f) if r.get(f) is not None else "") for f in c.PASSPORT_FIELDS},
                 "origin": "eservices" if sid else "official_other", "eservices_id": sid,
                 "action": "unchanged", "field_sources": {}, "conflicts": [],
                 "verification": "Verified", "sheet_row": r["row"]}
            cells = [dotgov_registry.parse_cell(r.get(f)) for f in c.DOTGOV_FIELDS]
            first = next((x for x in cells if x), None)
            if first:  # DotGov placeholder row (fully or partly unfilled)
                p["verification"] = c.DOTGOV_VERIFICATION
                p["dotgov"] = {"service_id": first["service_id"], "agency": first["agency"],
                               "service_name": first["service_name"], "match": "id", "from_sheet": True}
            passports.append(p)
        out.append({"ministry": ministry, "ministry_no": a.get("Ministry No.") or 0, "official_name": name,
                    "entity_type": a.get("Entity Type"), "status": a.get("Status") or "Included",
                    "digitizable_service_areas": a.get("Digitizable Service Areas") or "",
                    "source_link": a.get("Source Link") or c.ESERVICES_DIRECTORY_URL,
                    "eservices": {"status": c.ESERVICES_LISTED if any(p["eservices_id"] for p in passports) else c.ESERVICES_NOT_LISTED,
                                  "service_count": sum(1 for p in passports if p["eservices_id"]),
                                  "service_ids": [p["eservices_id"] for p in passports if p["eservices_id"]]},
                    "passports": passports})
    return out


def drift(agencies: list[dict], catalogue: dict, from_run: bool) -> list[dict]:
    """eServices services added/removed upstream compared with what the workbook covers."""
    items = []
    all_linked = {p.get("eservices_id") for a in agencies for p in a.get("passports", []) if p.get("eservices_id")}
    for a in agencies:
        es = a.get("eservices") or {}
        if from_run and es.get("authority_ids"):
            ids = es["authority_ids"]
            names = [n.strip() for n in (es.get("eservices_name") or "").split(";") if n.strip()]
        else:
            best = find_agency(catalogue, a["official_name"], limit=1)
            if not best or best[0]["score"] < PROVIDER_MATCH:
                continue
            ids, names = [best[0]["authority_id"]], []
        now = {s["ID"]: s["Title"] for s in agency_services(catalogue, ids, names)}
        known = set(es.get("service_ids") or [])
        handled = known | {x["eservices_id"] for x in es.get("excluded", [])}
        for sid, title in sorted(now.items()):
            if sid not in handled and sid not in all_linked:
                items.append({"agency": a["official_name"], "eservices_id": sid, "title": title,
                              "change": "on eServices, not in workbook"})
        for sid in sorted(known - set(now)):
            items.append({"agency": a["official_name"], "eservices_id": sid, "change": "no longer on eServices"})
    return items


def pick_sample(candidates: list[tuple[dict, dict]], n: int, focus: str, rng: random.Random) -> list[tuple[dict, dict]]:
    """focus=researcher_written: rows not taken straight from eServices first (they carry the judgement calls);
    eServices rows only fill what is left. focus=all: uniform over every row."""
    if focus == "all":
        return rng.sample(candidates, min(n, len(candidates)))
    primary = [x for x in candidates if x[1].get("origin") != "eservices"]
    rest = [x for x in candidates if x[1].get("origin") == "eservices"]
    chosen = rng.sample(primary, min(n, len(primary)))
    if len(chosen) < n:
        chosen += rng.sample(rest, min(n - len(chosen), len(rest)))
    return chosen


def prepare(workbook: Path, out: Path, run: Path | None, sample_n: int, seed: int | None,
            focus: str = "researcher_written", chunk: int = AUDIT_CHUNK) -> dict:
    data = read_workbook(workbook)
    catalogue = load_catalogue()
    slug_of: dict[int, str] = {}
    if run:
        agencies = []
        for path in sorted((run / "agencies").glob("*.json")):
            agencies.append(load_json(path))
            slug_of[id(agencies[-1])] = path.stem  # the run's own slug, so verifiers and evidence use one name
        build = load_json(run / "build.json") if (run / "build.json").exists() else {"changes": []}
        changed = set()  # rows changed by this run were just verified by the run itself; the audit samples independently
    else:
        agencies = agencies_from_sheet(data)
        changed = set()
    report = validate(agencies, dotgov_registry.for_run_dir(run) if run else "auto")
    findings = [f for f in report["findings"] if run or f["rule"] not in SHEET_ONLY_SKIP]
    errors = [f for f in findings if f["severity"] == "error"]

    rng = random.Random(seed)
    pool, picked, n_dotgov = [], [], 0
    for a in agencies:
        for p in a.get("passports", []):
            if p.get("action") == "remove" or p.get("verification") not in c.WRITABLE_VERIFICATION:
                continue
            if dotgov_registry.is_placeholder(p):  # DotGov data: not re-verified
                n_dotgov += 1
                continue
            key = (a["official_name"], c.normalise_name(p["service_name"]))
            (picked if key in changed else pool).append((a, p))
    picked += pick_sample(pool, sample_n, focus, rng)

    audit_run = out / "audit"
    if audit_run.exists():
        shutil.rmtree(audit_run)
    (audit_run / "agencies").mkdir(parents=True)
    (audit_run / "verify").mkdir()
    sample: dict[str, list[str]] = {}
    parts: dict[str, list[dict]] = {}
    for a in agencies:
        chosen = [p for (aa, p) in picked if aa is a]
        if not chosen:
            continue
        slug = slug_of.get(id(a)) or slugify(a["official_name"])
        dump_json({**a, "passports": chosen}, audit_run / "agencies" / f"{slug}.json")
        if run and (run / "evidence" / slug).exists():
            shutil.copytree(run / "evidence" / slug, audit_run / "evidence" / slug, dirs_exist_ok=True)
        sample[slug] = [p["service_name"] for p in chosen]
        if split_parts(len(chosen), chunk):
            parts[slug] = split_parts(len(chosen), chunk)
    result = {"workbook": str(workbook), "ministry": data.get("ministry"), "from_run": str(run) if run else None,
              "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "passport_errors": len(errors), "passport_warnings": len(findings) - len(errors),
              "findings": findings, "drift": drift(agencies, catalogue, bool(run)),
              "sample": sample, "parts": parts, "sample_size": len(picked), "dotgov_placeholders": n_dotgov}
    dump_json(result, out / "audit_prepare.json")
    log.info("prepared audit: errors=%d drift=%d sample=%d agencies=%d dotgov_placeholders=%d", len(errors),
             len(result["drift"]), len(picked), len(sample), n_dotgov)
    return result


def merge_parts(out: Path, parts: dict[str, list[dict]]) -> None:
    """Merge RUN/verify/<slug>.part<N>.json into <slug>.json once every expected part exists. An incomplete set
    is left alone, so the agency shows up as a missing verdict."""
    from agency_file import merge_verdicts

    audit_run = out / "audit"
    for slug, expected in parts.items():
        found = list((audit_run / "verify").glob(f"{slug}.part*.json"))
        if len(found) >= len(expected):
            merge_verdicts(audit_run, slug)
        else:
            log.warning("audit %s: %d of %d part verdicts present; not merged", slug, len(found), len(expected))


def finish(out: Path) -> dict:
    prep = load_json(out / "audit_prepare.json")
    wb = load_json(out / "workbook_audit.json") if (out / "workbook_audit.json").exists() else None
    merge_parts(out, prep.get("parts") or {})
    verdicts = {p.stem: load_json(p) for p in sorted((out / "audit" / "verify").glob("*.json"))
                if not p.name.endswith(".eservices.json") and not PART_FILE.search(p.name)}
    failed = [{"agency": v.get("agency"), **x} for v in verdicts.values() for x in v.get("passports", []) if x.get("verdict") == "FAIL"]
    limited = [{"agency": v.get("agency"), **x} for v in verdicts.values() for x in v.get("passports", [])
               if x.get("verdict") == "PASS_WITH_LIMITS"]
    missing_verdicts = sorted(set(prep["sample"]) - set(verdicts)
                              | {slug for slug, v in verdicts.items() if slug in prep["sample"]
                                 and len(v.get("passports", [])) < len(prep["sample"][slug])})
    wb_errors = wb["errors"] if wb else None
    ok = (wb_errors == 0 and prep["passport_errors"] == 0 and not failed and not prep["drift"] and not missing_verdicts)
    result = {"verdict": "PASS" if ok else "FAIL", "workbook": prep["workbook"], "workbook_errors": wb_errors,
              "passport_errors": prep["passport_errors"], "sample_size": prep["sample_size"],
              "sample_failed": len(failed), "sample_limited": len(limited), "drift": prep["drift"],
              "missing_verdicts": missing_verdicts, "failed": failed,
              "dotgov_placeholders": prep.get("dotgov_placeholders", 0)}
    dump_json(result, out / "audit.json")

    lines = [f"# Audit — {prep.get('ministry')}", "", f"**Verdict: {result['verdict']}**", "",
             f"- Workbook: `{prep['workbook']}`",
             f"- Workbook format/totals errors: {wb_errors if wb is not None else 'not run'}"
             + (f" ({', '.join(f'{k} {v}' for k, v in wb['by_rule'].items())})" if wb and wb.get("by_rule") else ""),
             f"- Passport rule errors: {prep['passport_errors']} (warnings {prep['passport_warnings']})",
             f"- Re-verified rows: {prep['sample_size']} — failed {len(failed)}, with limits {len(limited)}",
             f"- eServices drift: {len(prep['drift'])}",
             f"- DotGov placeholders (not re-verified; filled from the DotGov database): "
             f"{prep.get('dotgov_placeholders', 0)}", ""]
    if missing_verdicts:
        lines += [f"- ⚠️ No verifier result for: {', '.join(missing_verdicts)}", ""]
    if wb and wb["errors"]:
        lines += ["## Workbook errors", "", "| Sheet!Cell | Rule | Message |", "|---|---|---|"]
        lines += [f"| {f['sheet']}!{f['cell']} | {f['rule']} | {f['message']} |"
                  for f in wb["findings"] if f["severity"] == "error"][:200]
        lines.append("")
    errs = [f for f in prep["findings"] if f["severity"] == "error"]
    if errs:
        lines += ["## Passport rule errors", "", "| Agency | Service | Rule | Message | Fix |", "|---|---|---|---|---|"]
        lines += [f"| {f['agency']} | {(f.get('passport') or {}).get('service_name', '')} | {f['rule']} | {f['message']} | {f.get('fix', '')} |"
                  for f in errs]
        lines.append("")
    if failed:
        lines += ["## Re-verification failures", "", "| Agency | Service | Rules | Fix hint |", "|---|---|---|---|"]
        lines += [f"| {x['agency']} | {x.get('service_name')} | {', '.join(x.get('rules') or [])} | {x.get('fix_hint') or ''} |" for x in failed]
        lines.append("")
    if limited:
        lines += ["## Verified with limitations", "", *[f"- {x['agency']}: {x.get('service_name')} — {x.get('notes')}" for x in limited], ""]
    if prep["drift"]:
        lines += ["## eServices drift", "", *[f"- {d['agency']}: {d['eservices_id']} {d.get('title', '')} — {d['change']}" for d in prep["drift"]], ""]
    (out / "audit.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("audit %s: workbook_errors=%s passport_errors=%d failed=%d drift=%d", result["verdict"], wb_errors,
             prep["passport_errors"], len(failed), len(prep["drift"]))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prepare")
    pp.add_argument("workbook", type=Path)
    pp.add_argument("--out", type=Path, required=True)
    pp.add_argument("--run", type=Path)
    pp.add_argument("--sample", type=int, default=10)
    pp.add_argument("--seed", type=int)
    pp.add_argument("--focus", choices=("researcher_written", "all"), default="researcher_written")
    pp.add_argument("--chunk", type=int, default=AUDIT_CHUNK, help="max sampled rows per verifier; 0 = never split")
    fp = sub.add_parser("finish")
    fp.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.cmd == "prepare":
        r = prepare(args.workbook, args.out, args.run, args.sample, args.seed, args.focus, args.chunk)
        print(json.dumps({k: v for k, v in r.items() if k != "findings"}, ensure_ascii=False, indent=2))
        return 0
    r = finish(args.out)
    print(json.dumps({k: v for k, v in r.items() if k != "failed"}, ensure_ascii=False, indent=2))
    return 0 if r["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
