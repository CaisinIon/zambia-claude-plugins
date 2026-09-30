#!/usr/bin/env python3
"""Render the run report (Markdown) from a finished run folder.

Usage: report.py <RUN> [--out <RUN>/report.md]

Reads roster.json, agencies/*.json, verify/*.json, build.json,
workbook_validation.json and audit.json (when present). Per agency it shows
the items the directory brief requires: official name, eServices status,
exact eServices count, services verified on eServices, corrected passports,
additional services, source limitations / conflicts, historical-fee warnings,
passports to add, and the running ministry total.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import zmcontract as c
from zmlog import get_logger, load_json

log = get_logger("report")


def _load(path: Path, default=None):
    return load_json(path) if path.exists() else default


def _bullets(items, empty="none") -> str:
    items = [i for i in items if i]
    return "\n".join(f"  - {i}" for i in items) if items else f"  - {empty}"


def render(run: Path) -> str:
    meta = _load(run / "run.json", {})
    roster = _load(run / "roster.json", {"agencies": []})
    build = _load(run / "build.json", {})
    wbv = _load(run / "workbook_validation.json")
    audit = _load(run / "audit.json")
    changes = build.get("changes", [])
    order = [a["slug"] for a in roster["agencies"]]
    files = {p.stem: load_json(p) for p in sorted((run / "agencies").glob("*.json"))}
    slugs = [s for s in order if s in files] + [s for s in files if s not in order]

    lines = [f"# Run report — {meta.get('ministry', '?')}", "",
             f"- Ministry No.: {meta.get('ministry_no')}"
             + (" (newly assigned)" if meta.get("ministry_no_assigned") else ""),
             f"- Run: `{run}`  · eServices catalogue: {meta.get('catalogue_fetched_at')}",
             f"- Previous state: `{meta.get('previous_state')}`",
             f"- Workbook: `{build.get('workbook')}`",
             f"- Previous version archived: `{build.get('previous_version')}`", ""]
    if build:
        lines += ["## Totals", "",
                  "| Added | Corrected | Removed | Unchanged | Carried over (not re-checked) | Unresolved | Ministry total |",
                  "|---|---|---|---|---|---|---|",
                  f"| {build.get('added', 0)} | {build.get('corrected', 0)} | {build.get('removed', 0)} | "
                  f"{build.get('unchanged', 0)} | {build.get('carried_over', 0)} | {build.get('unresolved', 0)} | "
                  f"**{build.get('ministry_total', 0)}** |", ""]
    status = []
    if wbv is not None:
        status.append(f"Workbook validation: {'PASS' if wbv.get('ok') else 'FAIL'} "
                      f"({wbv.get('errors')} errors, {wbv.get('warnings')} warnings)")
    if audit is not None:
        status.append(f"Audit (/zm-verify-ministry): {audit.get('verdict')}")
    if status:
        lines += ["## Checks", "", *[f"- {s}" for s in status], ""]

    running = 0
    lines += ["## Agencies", ""]
    for slug in slugs:
        a = files[slug]
        name = a["official_name"]
        es = a.get("eservices") or {}
        verdict = _load(run / "verify" / f"{slug}.json", {})
        mine = [ch for ch in changes if ch.get("agency") == name]
        written = (build.get("per_agency") or {}).get(name, 0)
        running += written
        live = [p for p in a.get("passports", []) if p.get("action") != "remove"]
        es_ok = [p for p in live if p.get("origin") == "eservices" and p.get("verification") in c.WRITABLE_VERIFICATION]
        other = [p for p in live if p.get("origin") == "official_other" and p.get("verification") in c.WRITABLE_VERIFICATION]
        conflicts = [f"{p['service_name']}: {x['field']} — {x['description']} (used: {x['source_used']})"
                     for p in live for x in p.get("conflicts", [])]
        corrected = [f"{ch['service']} ({', '.join(ch.get('fields', []))})" for ch in mine if ch["change"] == "corrected"]
        added = [ch for ch in mine if ch["change"] == "added"]
        lines += [
            f"### {name}", "",
            f"- **Official agency name:** {name}" + (f" — acronym {a['acronym']}" if a.get("acronym") else ""),
            f"- **Zambia eServices status:** {es.get('status')}"
            + (f" (provider: {es.get('eservices_name')})" if es.get("eservices_name") else ""),
            f"- **Exact number of eServices services:** {es.get('service_count', 0)}"
            + (f"; excluded {len(es.get('excluded', []))}" if es.get("excluded") else "")
            + (f"; reassigned to other agencies {len(es.get('reassigned_out', []))}" if es.get("reassigned_out") else ""),
            f"- **Services verified on eServices:** {len(es_ok)}",
            "- **Corrected service passports:**", _bullets(corrected),
            "- **Additional services from other official sources:**", _bullets([p["service_name"] for p in other]),
            "- **Source limitations or conflicting information:**",
            _bullets([*a.get("source_limitations", []), *conflicts]),
            "- **Historical-fee warnings:**", _bullets(a.get("historical_fee_warnings", [])),
            f"- **Passports to be added:** {len(added)}",
            f"- **Verifier:** {verdict.get('verdict', 'not run')}",
            f"- **New ministry total after this agency:** {running}", "",
        ]
        unresolved = [ch["service"] for ch in mine if ch["change"] == "unresolved"]
        removed = [f"{ch['service']} — {ch.get('reason')}" for ch in mine if ch["change"] == "removed"]
        if unresolved:
            lines += ["- Unresolved (not written):", _bullets(unresolved)]
        if removed:
            lines += ["- Removed:", _bullets(removed)]
        lines.append("")
    carried = [ch for ch in changes if ch["change"] == "carried_over"]
    if carried:
        lines += ["## Carried over without re-check", "",
                  "These previous rows were not confirmed or removed in this run. They are kept unchanged.", "",
                  *[f"- {ch['agency']}: {ch['service']} (previous row {ch.get('previous_row')})" for ch in carried], ""]
    excluded = [a for a in roster["agencies"] if a.get("status") == "Excluded"]
    if excluded:
        lines += ["## Excluded entities", "", *[f"- {a['official_name']}: {a.get('reason')}" for a in excluded], ""]
    unmatched = roster.get("unmatched_eservices_authorities") or []
    if unmatched:
        lines += ["## eServices providers not matched to an agency", "",
                  *[f"- {u.get('name')} ({u.get('id')}): {u.get('reason')}" for u in unmatched], ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    text = render(args.run)
    out = args.out or args.run / "report.md"
    out.write_text(text, encoding="utf-8")
    log.info("report written to %s", out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
