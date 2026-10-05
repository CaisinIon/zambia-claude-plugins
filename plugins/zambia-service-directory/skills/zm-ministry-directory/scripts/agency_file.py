#!/usr/bin/env python3
"""Create, check and finalise one agency result file in a run.

Usage:
  agency_file.py init <RUN> <slug> [--force]
      Writes <RUN>/agencies/<slug>.json: agency fields from roster.json, eServices
      passport drafts (own services minus services reassigned to other agencies,
      plus services reassigned to this agency), and every previous-workbook row
      of this agency (matched to a draft, or as origin=imported to re-check).
      Services in the DotGov registry become placeholder passports (no detail fetch,
      no research, no verification); see dotgov_registry.py.
  agency_file.py check <RUN> <slug>
      Validate this agency (with ministry-wide duplicate checks against the other files).
  agency_file.py live-check <RUN> <slug>
      Re-fetch every eServices service of this agency fresh from the API (no cache) and compare
      name, fee, processing time and validity with the passports. Writes <RUN>/verify/<slug>.live.json.
      This replaces the LLM verifier's per-service live re-fetch.
  agency_file.py plan-verify <RUN> <slug> [--scope pending|all] [--chunk N]
      Which passports the verifier must cover and how to split them into parallel parts.
  agency_file.py merge-verdicts <RUN> <slug>
      Merge <RUN>/verify/<slug>.part*.json into <RUN>/verify/<slug>.json.
  agency_file.py apply-verdict <RUN> <slug> [--final]
      Apply <RUN>/verify/<slug>.json: PASS -> Verified, PASS_WITH_LIMITS ->
      Verified with limitations, FAIL -> stays Pending with notes (repair round),
      or Unresolved with --final. An unconfirmed removal is reverted.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import zmcontract as c
import dotgov_registry
import eservices
from eservices import build_agency, load_catalogue
from rapidfuzz import fuzz
from validate_passports import validate
from zmlog import dump_json, get_logger, load_json
from zmworkbook import eservices_id_from_link, read_workbook

log = get_logger("agency_file")
VERDICT_MAP = {"PASS": "Verified", "PASS_WITH_LIMITS": "Verified with limitations"}


def _roster_entry(run: Path, slug: str) -> tuple[dict, dict]:
    roster = load_json(run / "roster.json")
    for a in roster["agencies"]:
        if a.get("slug") == slug:
            return roster, a
    raise KeyError(f"slug {slug!r} not in roster")


def _previous_rows(run: Path, entry: dict) -> list[dict]:
    meta = load_json(run / "run.json")
    if not meta.get("previous_state"):
        return []
    names = {c.agency_key(n) for n in [entry["official_name"], *(entry.get("aliases") or [])]}
    rows = [r for r in read_workbook(Path(meta["previous_state"]))["passports"]
            if c.agency_key(r.get("agency") or r.get("section")) in names]
    dump_json(rows, run / "existing" / f"{entry['slug']}.json")
    return rows


def run_registry(meta: dict) -> dict | None:
    """The DotGov registry for this run (see dotgov_registry.for_run)."""
    return dotgov_registry.for_run(meta)


def _dotgov_previous(p: dict, snapshot: dict) -> None:
    """Previous workbook row for a DotGov placeholder. The placeholder always wins (the DotGov
    database is the source; re-run dotgov_fill.py after each run). Identical row -> unchanged;
    otherwise the old values (researched or filled) are replaced."""
    if all(snapshot.get(f) == p.get(f) for f in c.PASSPORT_FIELDS if f != "ministry"):
        p["action"] = "unchanged"
    else:
        p["action"], p["action_reason"] = "correct", "DotGov service: values come from the DotGov database"
        p["corrected_fields"] = [f for f in c.PASSPORT_FIELDS if f != "ministry" and snapshot.get(f) != p.get(f)]
    log.debug("dotgov previous row id=%s action=%s corrected=%s", p["eservices_id"], p["action"],
              p.get("corrected_fields"))


def init(run: Path, slug: str, force: bool = False) -> dict:
    out = run / "agencies" / f"{slug}.json"
    if out.exists() and not force:
        log.info("%s exists; keeping it (use --force to rebuild)", out)
        return load_json(out)
    meta = load_json(run / "run.json")
    roster, entry = _roster_entry(run, slug)
    ministry, name = meta["ministry"], entry["official_name"]
    catalogue = load_catalogue()
    registry = run_registry(meta)
    owned = dotgov_registry.by_id(registry)

    # services this agency gives away / receives (decided centrally in the roster)
    moved_in = entry.get("reassigned_services") or []
    moved_out = [{"eservices_id": r["eservices_id"], "to_agency": a["official_name"], "to_slug": a["slug"],
                  "reason": r["reason"]}
                 for a in roster["agencies"] if a is not entry for r in (a.get("reassigned_services") or [])]
    own = build_agency(catalogue, entry.get("eservices_authority_ids") or [],
                       [entry["eservices_name"]] if entry.get("eservices_name") else [],
                       ministry, name, meta.get("include_local", False), run / "evidence" / slug,
                       dotgov=registry)
    es = own["eservices"]
    own_ids = set(es["service_ids"])
    es["reassigned_out"] = [m for m in moved_out if m["eservices_id"] in own_ids]
    out_ids = {m["eservices_id"] for m in es["reassigned_out"]}
    passports = [p for p in own["passports"] if p["eservices_id"] not in out_ids]

    if moved_in:
        rows = [s for s in catalogue["services"] if s["ID"] in {m["eservices_id"] for m in moved_in}]
        for row in rows:
            m = next(x for x in moved_in if x["eservices_id"] == row["ID"])
            if row["ID"] in owned:
                p = eservices.to_dotgov_placeholder(row, owned[row["ID"]], ministry, name, registry)
            else:
                detail = eservices.fetch_detail(row["ID"])
                ev_path, ev_sha = eservices.save_evidence(row, detail, run / "evidence" / slug)
                p = eservices.to_passport(row, detail, ministry, name, ev_path, ev_sha)
            p.update(reassigned_from=m["from_provider"], reassignment_reason=m["reason"])
            passports.append(p)

    # previous workbook rows: attach to matching drafts, else import for re-checking
    prev = _previous_rows(run, entry)
    by_id = {p["eservices_id"]: p for p in passports if p.get("eservices_id")}
    for r in prev:
        sid = eservices_id_from_link(r.get("source_link"))
        snapshot = {f: r.get(f) for f in c.PASSPORT_FIELDS}
        if sid and sid in by_id:
            by_id[sid]["previous_row"] = snapshot
            if by_id[sid].get("dotgov"):
                _dotgov_previous(by_id[sid], snapshot)
            continue
        match = next((p for p in passports if c.normalise_name(p["service_name"]) == c.normalise_name(r.get("service_name"))), None)
        if match:
            match["previous_row"] = snapshot
            continue
        passports.append({**{f: (r.get(f) or c.NOT_PUBLISHED) for f in c.PASSPORT_FIELDS},
                          "ministry": ministry, "agency": name, "origin": "imported", "eservices_id": sid,
                          "reassigned_from": None, "reassignment_reason": None, "action": "unchanged",
                          "action_reason": None, "historical_fee": False, "field_sources": {}, "conflicts": [],
                          "verification": "Pending", "verification_notes": "re-check against official sources",
                          "previous_row": snapshot, "previous_name": r.get("service_name")})

    agency = {
        "slug": slug, "roster_name": name, "aliases": list(entry.get("aliases") or []),
        "ministry": ministry, "ministry_no": meta["ministry_no"], "official_name": name,
        "acronym": entry.get("acronym"), "entity_type": entry["entity_type"], "status": entry["status"],
        "status_reason": entry.get("reason") if entry["status"] == "Excluded" else None,
        "digitizable_service_areas": "", "source_link": c.ESERVICES_DIRECTORY_URL if (es["service_count"] or moved_in) else (entry.get("website") or ""),
        "website": entry.get("website"), "eservices": es, "catalogue_fetched_at": own.get("catalogue_fetched_at"),
        "other_sources_summary": None, "notes": None, "source_limitations": [], "historical_fee_warnings": [],
        "searched_sources": [], "passports": passports,
    }
    dump_json(agency, out)
    n_dotgov = sum(1 for p in passports if p.get("dotgov"))
    log.info("init %s: eServices=%d (out %d, in %d) dotgov_placeholders=%d drafts=%d previous_rows=%d passports=%d",
             slug, es["service_count"], len(out_ids), len(moved_in), n_dotgov,
             sum(1 for p in passports if p.get("origin") == "eservices" and not p.get("dotgov")),
             len(prev), len(passports))
    return agency


def check(run: Path, slug: str) -> dict:
    files = sorted((run / "agencies").glob("*.json"))
    agencies = [load_json(f) for f in files]
    target = load_json(run / "agencies" / f"{slug}.json")
    report = validate(agencies, run_registry(load_json(run / "run.json")))
    mine = [f for f in report["findings"] if f["agency"] == target["official_name"]]
    errors = sum(1 for f in mine if f["severity"] == "error")
    return {"ok": errors == 0, "errors": errors, "warnings": len(mine) - errors,
            "summary": report["summary"].get(target["official_name"]), "findings": mine}


def apply_verdict(run: Path, slug: str, final: bool = False) -> dict:
    path = run / "agencies" / f"{slug}.json"
    agency = load_json(path)
    verdict = load_json(run / "verify" / f"{slug}.json")
    results = {"Verified": 0, "Verified with limitations": 0, "repair": 0, "Unresolved": 0}
    for v in verdict.get("passports", []):
        i = v["index"]
        if i >= len(agency["passports"]):
            log.warning("verdict index %d out of range for %s", i, slug)
            continue
        p = agency["passports"][i]
        if dotgov_registry.is_placeholder(p):
            log.warning("verdict %d for DotGov placeholder %r ignored (never verified)", i, p["service_name"])
            continue
        if v.get("service_name") and c.normalise_name(v["service_name"]) != c.normalise_name(p["service_name"]):
            log.warning("verdict %d name %r != passport %r; skipped", i, v["service_name"], p["service_name"])
            continue
        notes = "; ".join(filter(None, [", ".join(v.get("rules") or []), v.get("notes"), v.get("fix_hint")]))
        if v["verdict"] in VERDICT_MAP:
            p["verification"] = VERDICT_MAP[v["verdict"]]
            p["verification_notes"] = notes or None
            p.pop("draft_flags", None)
            results[p["verification"]] += 1
        elif p.get("action") == "remove":
            # removal not confirmed: keep the previous row (carried over by the builder)
            p.update(action="unchanged", verification="Unresolved",
                     verification_notes=f"removal not confirmed by verifier: {notes}")
            results["Unresolved"] += 1
        elif final:
            p.update(verification="Unresolved", verification_notes=notes)
            results["Unresolved"] += 1
        else:
            p.update(verification="Pending", verification_notes=f"REPAIR: {notes}")
            results["repair"] += 1
    if verdict.get("missing_services"):
        agency.setdefault("source_limitations", []).extend(
            f"verifier: possible missing service {m}" for m in verdict["missing_services"])
    dump_json(agency, path)
    log.info("verdict applied %s final=%s %s", slug, final, results)
    return {"slug": slug, "final": final, **results, "agency_verdict": verdict.get("verdict")}


_NORM_RE = re.compile(r"[^a-z0-9]+")
_WORD_NUM_RE = re.compile(r"\b[a-z]+(?:[- ][a-z]+)?\s*\((\d+)\)", re.I)  # "Three (3)" -> "3"


def _norm(text) -> str:
    """Compare key: lower-case, spelled-out numbers like 'Three (3)' reduced to '3', punctuation and spaces dropped."""
    text = _WORD_NUM_RE.sub(r"\1", str(text or ""))
    return _NORM_RE.sub("", text.lower())


def _field_status(file_value, live_value) -> str:
    """match: identical after punctuation/case; close: similar wording; differs: needs a human/LLM look."""
    a, b = _norm(file_value), _norm(live_value)
    if a == b:
        return "match"
    if a in ("notpublished", "") and b == "notpublished":
        return "match"
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return "differs"  # any change in a number (amount, period) is never merely 'close'
    words = lambda v: re.sub(r"[^a-z0-9]+", " ", _WORD_NUM_RE.sub(r"\1", str(v or "")).lower()).strip()  # noqa: E731
    return "close" if fuzz.token_sort_ratio(words(file_value), words(live_value)) >= 85 else "differs"


def live_check(run: Path, slug: str) -> dict:
    """Fresh eServices comparison for one agency (deterministic; no LLM)."""
    agency = load_json(run / "agencies" / f"{slug}.json")
    catalogue = eservices.load_catalogue(refresh=True)  # fresh: do not reuse today's cache
    es = agency.get("eservices") or {}
    names = [n.strip() for n in (es.get("eservices_name") or "").split(";") if n.strip()]
    live_rows = eservices.agency_services(catalogue, es.get("authority_ids") or [], names)
    live_ids = {r["ID"] for r in live_rows}
    file_ids = set(es.get("service_ids") or [])
    tmp = run / "verify" / f"{slug}.live-cache"
    results, problems = [], 0
    by_id = {r["ID"]: r for r in catalogue["services"]}
    wanted = [p["eservices_id"] for p in agency["passports"]
              if p.get("origin") == "eservices" and p.get("eservices_id") in by_id and p.get("action") != "remove"
              and not dotgov_registry.is_placeholder(p)]
    with ThreadPoolExecutor(max_workers=eservices.MAX_WORKERS) as pool:  # same 4-request limit as the client
        list(pool.map(lambda i: eservices.fetch_detail(i, tmp), wanted))
    for i, p in enumerate(agency["passports"]):
        sid = p.get("eservices_id")
        if p.get("origin") != "eservices" or not sid or p.get("action") == "remove":
            continue
        row = by_id.get(sid)
        if row is None:
            results.append({"index": i, "eservices_id": sid, "status": "gone_from_eservices"})
            problems += 1
            continue
        if dotgov_registry.is_placeholder(p):
            results.append({"index": i, "eservices_id": sid, "status": "dotgov"})
            log.debug("live-check %s id=%s DotGov placeholder: catalogue presence only", slug, sid)
            continue
        detail = eservices.fetch_detail(sid, tmp)
        fresh = eservices.to_passport(row, detail, p["ministry"], p["agency"])
        fields = {}
        for f in ("service_name", "fee", "processing_time", "validity"):
            fields[f] = {"status": _field_status(p.get(f), fresh[f]), "file": p.get(f), "live": fresh[f]}
        saved = (p.get("eservices_meta") or {}).get("modified_on")
        live_mod = (detail.get("info") or {}).get("ModifiedOn")
        upstream = bool(saved and live_mod and saved != live_mod)
        bad = [f for f, v in fields.items() if v["status"] != "match"] or upstream
        problems += 1 if bad else 0
        results.append({"index": i, "eservices_id": sid, "status": "check" if bad else "ok",
                        "upstream_changed": upstream, "fields": fields})
    out = {"agency": agency["official_name"], "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "count_file": es.get("service_count"), "count_live": len(live_ids),
           "count_ok": len(live_ids) == es.get("service_count", 0) and live_ids == file_ids,
           "count_missing_in_file": sorted(live_ids - file_ids), "count_missing_live": sorted(file_ids - live_ids),
           "passports": results, "needs_attention": problems,
           "note": "status ok = all four fields match the live API; check = read fields with status close/differs; "
                   "dotgov = DotGov placeholder, only its presence in the catalogue is checked"}
    dump_json(out, run / "verify" / f"{slug}.live.json")
    log.info("live-check %s: checked=%d attention=%d count_ok=%s", slug, len(results), problems, out["count_ok"])
    return out


def plan_verify(run: Path, slug: str, scope: str = "all", chunk: int = 0) -> dict:
    agency = load_json(run / "agencies" / f"{slug}.json")
    # DotGov placeholders are never verified: their data comes from the DotGov database
    skipped = [i for i, p in enumerate(agency["passports"]) if dotgov_registry.is_placeholder(p)]
    idx = [i for i, p in enumerate(agency["passports"])
           if i not in skipped and (scope == "all" or p.get("verification") == "Pending")]
    size = chunk if chunk and chunk > 0 else max(len(idx), 1)
    parts = [idx[i:i + size] for i in range(0, len(idx), size)] or [[]]
    plan = {"slug": slug, "scope": scope, "total_passports": len(agency["passports"]),
            "to_verify": len(idx), "dotgov_skipped": len(skipped),
            "chunks": [{"part": n + 1, "indices": ids} for n, ids in enumerate(parts)],
            "agency_checks_in_part": 1}
    log.info("plan-verify %s scope=%s to_verify=%d dotgov_skipped=%d chunks=%d", slug, scope, len(idx),
             len(skipped), len(parts))
    return plan


def _count_flag(values: list) -> bool | None:
    """Parts that skipped the agency-level checks write null; only real answers count."""
    answered = [v for v in values if v is not None]
    return all(answered) if answered else None


def merge_verdicts(run: Path, slug: str) -> dict:
    files = sorted((run / "verify").glob(f"{slug}.part*.json"), key=lambda f: int(re.search(r"part(\d+)", f.name).group(1)))
    if not files:
        raise FileNotFoundError(f"no part files for {slug} in {run / 'verify'}")
    parts = [load_json(f) for f in files]
    passports = sorted((p for part in parts for p in part.get("passports", [])), key=lambda p: p["index"])
    seen = [p["index"] for p in passports]
    dupes = sorted({i for i in seen if seen.count(i) > 1})
    if dupes:
        raise ValueError(f"passport indices verified more than once: {dupes}")
    verdicts = [p["verdict"] for p in passports]
    overall = "FAIL" if "FAIL" in verdicts else ("PASS_WITH_LIMITS" if "PASS_WITH_LIMITS" in verdicts else "PASS")
    merged = {
        "agency": parts[0].get("agency"), "verdict": overall, "checked_at": parts[-1].get("checked_at"),
        "eservices_count_ok": _count_flag([part.get("eservices_count_ok") for part in parts]),
        "passports": passports,
        "missing_services": sorted({m for part in parts for m in part.get("missing_services", [])}),
        "notes": " | ".join(filter(None, (part.get("notes") for part in parts))),
        "merged_from": [f.name for f in files],
    }
    dump_json(merged, run / "verify" / f"{slug}.json")
    log.info("merged %d parts for %s: %d passports verdict=%s", len(files), slug, len(passports), overall)
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "check", "apply-verdict", "live-check", "plan-verify", "merge-verdicts"):
        p = sub.add_parser(name)
        p.add_argument("run", type=Path)
        p.add_argument("slug")
        if name == "plan-verify":
            p.add_argument("--scope", choices=("pending", "all"), default="all")
            p.add_argument("--chunk", type=int, default=0)
        if name == "init":
            p.add_argument("--force", action="store_true")
        if name == "apply-verdict":
            p.add_argument("--final", action="store_true")
    args = ap.parse_args()
    try:
        if args.cmd == "init":
            a = init(args.run, args.slug, args.force)
            print(json.dumps({"file": str(args.run / "agencies" / f"{args.slug}.json"),
                              "eservices_count": a["eservices"]["service_count"],
                              "passports": len(a["passports"]),
                              "imported_to_recheck": sum(1 for p in a["passports"] if p["origin"] == "imported")},
                             ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "check":
            r = check(args.run, args.slug)
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 0 if r["ok"] else 1
        if args.cmd == "live-check":
            r = live_check(args.run, args.slug)
            print(json.dumps({k: v for k, v in r.items() if k != "passports"} | {
                "attention": [x for x in r["passports"] if x["status"] != "ok"]}, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "plan-verify":
            print(json.dumps(plan_verify(args.run, args.slug, args.scope, args.chunk), indent=2))
            return 0
        if args.cmd == "merge-verdicts":
            m = merge_verdicts(args.run, args.slug)
            print(json.dumps({"agency": m["agency"], "verdict": m["verdict"], "passports": len(m["passports"])}))
            return 0
        r = apply_verdict(args.run, args.slug, args.final)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    except (KeyError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
