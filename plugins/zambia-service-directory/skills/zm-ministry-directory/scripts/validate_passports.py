#!/usr/bin/env python3
"""Deterministic checks on agency result files (agency.schema.json).

Usage: validate_passports.py <agency.json> [<agency.json> ...] [--out report.json]

Checks every passport and the ministry as a whole. Prints a JSON report.
Exit 0 = no errors (warnings allowed), 1 = errors found, 2 = bad input.

Rule ids (severity):
  R-SCHEMA (error)    schema violation
  R-TERM (error)      deprecated placeholder, e.g. 'Not specified' -> 'Not published'
  R-TERM-VAGUE (warn) vague placeholder ('unknown', 'TBC', ...)
  R-DUP-ID (error)    same eServices ID used twice in the ministry
  R-DUP-NAME (error)  near-identical service names in one agency (warn if both have distinct eServices IDs)
  R-COUNT (error)     eServices services of the agency not covered, or foreign IDs without reassignment
  R-REASSIGN (error)  reassigned passport without reason / not recorded by the source agency
  R-SOURCE (error)    sourced field without a tier<=4 source; (warn) placeholder without a checked source
  R-FEE-HIST (error)  historical fee not labelled
  R-LAW (warn)        legal reference without Act/SI/Cap number
  R-LINK (error)      non-https link or bare homepage without link_reason
  R-AGENCY (error)    passport ministry/agency differs from its agency file; eServices status/count mismatch
  R-ENTITY (warn)     entity type not in the fixed list
  R-SCOPE (warn)      service name looks internal (recruitment, tender, ...)
  R-ELIG-LEN (warn)   eligibility longer than 600 characters (summarise)
  R-DRAFT (warn)      draft_flags left on a passport marked Verified
  R-PENDING (warn)    passport not yet verified
  R-REMOVE (error)    removal without action_reason (removed passports skip all other passport rules)
  R-DOTGOV (error)    DotGov placeholder malformed (status, ID, marker/token), or a token in a normal passport
  R-DOTGOV-DUP (error) web/imported service name-matches a DotGov service of the agency (warn: possible match,
                      or dotgov_distinct_reason given)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import dotgov_registry
import zmcontract as c
from zmlog import dump_json, get_logger, load_json
from zmschema import schema_errors

log = get_logger("validate_passports")

VAGUE = re.compile(r"^(unknown|tbc|tba|to be confirmed|not available|none|nil|-+|\?)$", re.I)
OUT_OF_SCOPE = re.compile(r"\b(vacanc|recruit|tender|procure|internship(?!\s+site)|job|staff|employee welfare|bid)\w*", re.I)
MAX_ELIGIBILITY = 600


class Findings:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, rule, severity, agency, message, passport=None, fix=None):
        if passport is not None and passport.get("verification") == "Unresolved" and severity == "error":
            severity = "warn"  # never written to the workbook, so it must not block the build
            message = f"[unresolved, not written] {message}"
        item = {"rule": rule, "severity": severity, "agency": agency, "message": message}
        if passport is not None:
            item["passport"] = {"service_name": passport.get("service_name"),
                                "eservices_id": passport.get("eservices_id")}
        if fix:
            item["fix"] = fix
        self.items.append(item)
        log.debug("%s %s | %s | %s", rule, severity, agency, message)


# words that mark a channel / form variant, not a different service
CHANNEL_WORDS = {"online", "offline", "manual", "form", "forms", "application", "apply", "applying", "e", "service",
                 "services", "via", "payment", "pay", "office", "portal", "web", "mobile", "submission", "request",
                 "for", "a", "an", "the", "of", "to", "and", "in", "at", "on"}


def is_duplicate_name(x: str, y: str) -> bool:
    """Same service name, or names that differ only by channel/noise words.

    Character similarity is not used: 'Export' vs 'Import' or 'Camping' vs 'Canoeing'
    are different services even though the strings look alike.
    """
    a, b = c.normalise_name(x), c.normalise_name(y)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    diff = ta ^ tb
    core_a, core_b = ta - CHANNEL_WORDS, tb - CHANNEL_WORDS
    return bool(core_a) and core_a == core_b and diff <= CHANNEL_WORDS


def _is_placeholder(value: str) -> bool:
    return value in c.PLACEHOLDERS


def check_dotgov(p: dict, agency: dict, f: Findings) -> bool:
    """R-DOTGOV. Returns True when p is a well-formed DotGov placeholder (its fields need no sources)."""
    name = agency.get("official_name", "?")
    if not dotgov_registry.is_placeholder(p):
        for field in c.PASSPORT_FIELDS:
            if dotgov_registry.ANY_TOKEN_RE.search(str(p.get(field) or "")):
                f.add("R-DOTGOV", "error", name, f"{field} holds a DotGov token but the passport is not a DotGov "
                      "placeholder", p, fix="remove the token, or rebuild the passport with agency_file.py init")
        return False
    dg = p.get("dotgov")
    if not dg:
        f.add("R-DOTGOV", "error", name, f"verification {c.DOTGOV_VERIFICATION!r} without a dotgov record", p)
        return False
    ok = True
    sid = p.get("eservices_id")
    if p.get("verification") != c.DOTGOV_VERIFICATION:
        f.add("R-DOTGOV", "error", name, f"DotGov passport has verification {p.get('verification')!r}", p,
              fix=c.DOTGOV_VERIFICATION)
        ok = False
    if p.get("origin") != "eservices" or sid != dg.get("service_id"):
        f.add("R-DOTGOV", "error", name, f"DotGov passport must have origin 'eservices' and eservices_id "
              f"{dg.get('service_id')} (has {p.get('origin')!r}/{sid})", p)
        ok = False
    service = {"service_id": dg.get("service_id"), "agency": dg.get("agency"), "name": dg.get("service_name")}
    for field in c.DOTGOV_FIELDS:
        value = str(p.get(field) or "").strip()
        if dg.get("from_sheet"):  # rebuilt from a workbook: a filled cell is fine, a token must fit the row
            cell = dotgov_registry.parse_cell(value)
            bad = (cell and (cell["service_id"] != sid or cell["field"] != field)) or \
                (not cell and dotgov_registry.ANY_TOKEN_RE.search(value))
        else:
            bad = value != dotgov_registry.placeholder(service, field)
        if bad:
            f.add("R-DOTGOV", "error", name, f"{field} is not the DotGov placeholder for service {sid}: {value!r}", p,
                  fix=None if dg.get("from_sheet") else dotgov_registry.placeholder(service, field))
            ok = False
    return ok


def check_dotgov_dup(p: dict, agency: dict, registry: dict | None, f: Findings) -> None:
    """R-DOTGOV-DUP: a service found on the web / carried over that is really a DotGov service."""
    if not registry or dotgov_registry.is_placeholder(p) or p.get("origin") not in ("official_other", "imported"):
        return
    m = dotgov_registry.match(registry, agency, p.get("service_name") or "")
    if m["status"] == "none":
        return
    reason = p.get("dotgov_distinct_reason")
    severity = "error" if m["status"] == "match" and not reason else "warn"
    f.add("R-DOTGOV-DUP", severity, agency.get("official_name", "?"),
          f"{p.get('service_name')!r} is a {m['status']} for DotGov service {m['service_id']} {m['name']!r} "
          f"({m['agency']}, score {m['score']})" + (f"; distinct: {reason}" if reason else ""), p,
          fix=None if reason else f"remove it (DotGov service {m['service_id']}), or set dotgov_distinct_reason")


def check_passport(p: dict, agency: dict, f: Findings) -> None:
    name = agency.get("official_name", "?")
    for err in schema_errors(p, "passport"):
        f.add("R-SCHEMA", "error", name, err, p)
    if p.get("ministry") != agency.get("ministry") or p.get("agency") != agency.get("official_name"):
        f.add("R-AGENCY", "error", name,
              f"passport ministry/agency {p.get('ministry')!r}/{p.get('agency')!r} != agency file", p)

    dotgov_ok = check_dotgov(p, agency, f)
    sources = p.get("field_sources") or {}
    for field in c.PASSPORT_FIELDS:
        value = str(p.get(field) or "").strip()
        if value in c.DEPRECATED_PLACEHOLDERS:
            f.add("R-TERM", "error", name, f"{field} uses deprecated {value!r}", p,
                  fix=c.DEPRECATED_PLACEHOLDERS[value])
        elif value and VAGUE.match(value):
            f.add("R-TERM-VAGUE", "warn", name, f"{field} has vague value {value!r}", p,
                  fix=f"{c.NOT_PUBLISHED} (if checked) or the real value")
    for field in c.SOURCED_FIELDS:
        if dotgov_ok and field in c.DOTGOV_FIELDS:
            continue  # DotGov database values, filled later
        value = str(p.get(field) or "").strip()
        tiers = [s.get("tier", 9) for s in sources.get(field, [])]
        if _is_placeholder(value):
            if not tiers:
                f.add("R-SOURCE", "warn", name, f"{field}={value!r} but no checked source recorded", p)
        elif not tiers or min(tiers) > c.MAX_CITABLE_TIER:
            f.add("R-SOURCE", "error", name,
                  f"{field} has no official source (tiers={tiers}); tier 5 is lead-only", p)

    fee = str(p.get("fee") or "")
    if p.get("historical_fee") and not fee.startswith(c.HISTORICAL_FEE):
        f.add("R-FEE-HIST", "error", name, "historical_fee=true but fee is not labelled", p,
              fix=f"{c.HISTORICAL_FEE}. <old amount and year>")
    if fee.startswith(c.HISTORICAL_FEE) and not p.get("historical_fee"):
        f.add("R-FEE-HIST", "warn", name, "fee labelled historical but historical_fee=false", p)

    legal = str(p.get("legal_references") or "")
    if legal and not _is_placeholder(legal) and not dotgov_ok:
        for part in [x.strip() for x in legal.split(";") if x.strip()]:
            if not any(pat.search(part) for pat in c.LEGAL_REF_PATTERNS):
                f.add("R-LAW", "warn", name, f"legal reference without Act/SI/Cap number: {part!r}", p)

    link = str(p.get("source_link") or "")
    parsed = urlparse(link)
    if parsed.scheme != "https":
        f.add("R-LINK", "error" if parsed.scheme != "http" else "warn", name, f"link is not https: {link!r}", p)
    if parsed.path in ("", "/") and not parsed.query and not parsed.fragment and not p.get("link_reason"):
        f.add("R-LINK", "error", name, f"bare homepage link without link_reason: {link!r}", p)

    if OUT_OF_SCOPE.search(str(p.get("service_name") or "")):
        f.add("R-SCOPE", "warn", name, "service name looks internal/out of scope", p)
    if len(str(p.get("eligibility_requirements") or "")) > MAX_ELIGIBILITY:
        f.add("R-ELIG-LEN", "warn", name, "eligibility longer than 600 chars; summarise to 1–3 sentences", p)
    if p.get("draft_flags") and p.get("verification") in c.WRITABLE_VERIFICATION:
        f.add("R-DRAFT", "warn", name, f"draft_flags not cleared: {p['draft_flags']}", p)
    if p.get("verification") == "Pending":
        f.add("R-PENDING", "warn", name, "passport not verified yet", p)


def check_agency(agency: dict, f: Findings, registry: dict | None = None) -> None:
    name = agency.get("official_name", "?")
    stripped = {k: v for k, v in agency.items() if k != "passports"}
    for err in schema_errors({**stripped, "passports": []}, "agency"):
        f.add("R-SCHEMA", "error", name, err)
    if agency.get("entity_type") not in c.ENTITY_TYPES:
        f.add("R-ENTITY", "warn", name, f"entity type {agency.get('entity_type')!r} not in fixed list")
    es = agency.get("eservices") or {}
    count = es.get("service_count", 0)
    listed = es.get("status") == c.ESERVICES_LISTED
    if listed != (count > 0):
        f.add("R-AGENCY", "error", name, f"eServices status {es.get('status')!r} but service_count={count}")
    ids = es.get("service_ids") or []
    if ids and len(ids) != count:
        f.add("R-AGENCY", "error", name, f"service_count={count} but {len(ids)} service_ids")

    passports = [p for p in agency.get("passports", []) if p.get("action") != "remove"]
    for p in agency.get("passports", []):
        if p.get("action") == "remove":
            # a removal only needs its reason; the row is not written
            if not p.get("action_reason"):
                f.add("R-REMOVE", "error", name, "action=remove without action_reason", p)
            continue
        check_passport(p, agency, f)
        check_dotgov_dup(p, agency, registry, f)

    # duplicates by name inside the agency
    for i, a in enumerate(passports):
        for b in passports[i + 1:]:
            if is_duplicate_name(a.get("service_name", ""), b.get("service_name", "")):
                distinct_ids = a.get("eservices_id") and b.get("eservices_id") and a["eservices_id"] != b["eservices_id"]
                f.add("R-DUP-NAME", "warn" if distinct_ids else "error", name,
                      f"duplicate service names: {a.get('service_name')!r} / {b.get('service_name')!r}", b)

    # eServices coverage
    excluded = {x["eservices_id"] for x in es.get("excluded", [])}
    moved_out = {x["eservices_id"] for x in es.get("reassigned_out", [])}
    expected = set(ids) - excluded - moved_out
    own = {p.get("eservices_id") for p in passports if p.get("origin") == "eservices" and not p.get("reassigned_from")}
    for missing in sorted(expected - own):
        f.add("R-COUNT", "error", name, f"eServices service {missing} has no passport, exclusion or reassignment")
    for p in passports:
        sid = p.get("eservices_id")
        if p.get("origin") == "eservices" and not p.get("reassigned_from") and sid not in set(ids):
            f.add("R-COUNT", "error", name, f"eServices ID {sid} is not one of this agency's services and not reassigned", p)
        if sid in excluded or sid in moved_out:
            f.add("R-COUNT", "error", name, f"eServices ID {sid} is both a passport and excluded/reassigned out", p)


def check_ministry(agencies: list[dict], f: Findings) -> None:
    ministries = {a.get("ministry") for a in agencies}
    if len(ministries) > 1:
        f.add("R-AGENCY", "error", "*", f"agency files belong to several ministries: {sorted(ministries)}")
    seen: dict[int, str] = {}
    by_name = {a.get("official_name"): a for a in agencies}
    for a in agencies:
        for p in a.get("passports", []):
            if p.get("action") == "remove":
                continue
            sid = p.get("eservices_id")
            if sid:
                if sid in seen:
                    f.add("R-DUP-ID", "error", a.get("official_name"),
                          f"eServices ID {sid} also used by {seen[sid]!r}", p)
                seen[sid] = a.get("official_name")
            src = p.get("reassigned_from")
            if src:
                if not p.get("reassignment_reason"):
                    f.add("R-REASSIGN", "error", a.get("official_name"), "reassigned passport without reason", p)
                donors = [d for d in agencies if src in (d.get("official_name"), (d.get("eservices") or {}).get("eservices_name"))]
                for d in donors:
                    outs = {(x["eservices_id"], x["to_agency"]) for x in (d.get("eservices") or {}).get("reassigned_out", [])}
                    outs |= {(x["eservices_id"], x["to_slug"]) for x in (d.get("eservices") or {}).get("reassigned_out", [])
                             if x.get("to_slug")}
                    if (sid, a.get("official_name")) not in outs and (sid, a.get("slug")) not in outs:
                        f.add("R-REASSIGN", "error", a.get("official_name"),
                              f"{d.get('official_name')!r} does not list ID {sid} in reassigned_out", p)
                if not donors and src not in by_name:
                    log.debug("reassignment source %r not among given agency files", src)


def validate(agencies: list[dict], registry: dict | None | str = "auto") -> dict:
    """registry: the DotGov registry for R-DOTGOV-DUP; "auto" loads the default, None skips the rule."""
    if registry == "auto":
        registry = dotgov_registry.load()
    f = Findings()
    for a in agencies:
        check_agency(a, f, registry)
    check_ministry(agencies, f)
    errors = sum(1 for x in f.items if x["severity"] == "error")
    warnings = len(f.items) - errors
    summary = {}
    for a in agencies:
        live = [p for p in a.get("passports", []) if p.get("action") != "remove"]
        summary[a.get("official_name")] = {
            "passports": len(live),
            "eservices": sum(1 for p in live if p.get("origin") == "eservices"),
            "other": sum(1 for p in live if p.get("origin") != "eservices"),
            "writable": sum(1 for p in live if p.get("verification") in c.WRITABLE_VERIFICATION),
            "dotgov_placeholders": sum(1 for p in live if dotgov_registry.is_placeholder(p)),
            "eservices_count": (a.get("eservices") or {}).get("service_count", 0),
        }
    log.info("validation done agencies=%d errors=%d warnings=%d", len(agencies), errors, warnings)
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "summary": summary, "findings": f.items}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    try:
        agencies = [load_json(p) for p in args.files]
    except (OSError, json.JSONDecodeError) as exc:
        log.error("cannot read input: %s", exc)
        return 2
    report = validate(agencies)
    if args.out:
        dump_json(report, args.out)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
