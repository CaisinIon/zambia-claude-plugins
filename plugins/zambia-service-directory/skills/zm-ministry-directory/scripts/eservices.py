#!/usr/bin/env python3
"""Zambia eServices client (public JSON API behind https://eservices.gov.zm).

Commands:
  catalogue [--refresh]                         cache authorities + services for today
  find-agency "<name or acronym>" [--limit 8]   fuzzy-match eServices providers
  agency --authority-id ID [--authority-id ID] [--name "Provider"] --ministry M --agency A
         [--include-local] [--evidence DIR] [--out FILE] [--no-dotgov]
                                                exact service count + passport drafts
                                                (DotGov services: placeholders, no detail fetch)
  service <ID> [--evidence DIR]                 one service's raw detail (info, lex, docs)

API base: $ZM_ESERVICES_API (default https://zigsapi.eservices.gov.zm/).
Cache: <project>/cache/eservices/<YYYY-MM-DD>/.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from rapidfuzz import fuzz
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import dotgov_registry
import zmcontract as c
from zmlog import dump_json, get_logger, kv, load_json, project_root

log = get_logger("eservices")

API_BASE = os.environ.get("ZM_ESERVICES_API", "https://zigsapi.eservices.gov.zm/").rstrip("/") + "/"
AUTHORITIES_PATH = "public/data/AvailableAuthorities?page=0&limit=0&start=0"
SERVICES_PATH = "public/data/AvailablePermitsNew?page=0&limit=0&start=0"
INFO_PATH = "passport/data/PermitInfo/{id}"
LEX_PATH = "passport/data/PermitLex?page=0&start=0&limit=0&filter={filter}"
DOCS_PATH = "passport/data/PermitSupportingDocuments?page=0&start=0&limit=0&filter={filter}"
USER_AGENT = "zm-service-directory-harness/1.0 (research; contact: project maintainer)"
TIMEOUT = 30
MAX_WORKERS = 4

BOILERPLATE_MARKERS = ("zampass", "apply for service", "click on", "digitally sign", "pin code",
                       "payment receipt", "log in", "logged on", "register first")
LEGAL_HINT = re.compile(r"\b(act|cap\.?|statutory instrument|regulations?|si no|constitution|order)\b", re.I)


# ---------------------------------------------------------------- HTTP / cache

def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=MAX_WORKERS))
    s.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
    return s


_SESSION: requests.Session | None = None


def get_json(path: str):
    global _SESSION
    if _SESSION is None:
        _SESSION = _session()
    url = API_BASE + path
    started = time.monotonic()
    resp = _SESSION.get(url, timeout=TIMEOUT)
    log.debug("GET %s status=%s bytes=%d ms=%d", url, resp.status_code, len(resp.content),
              (time.monotonic() - started) * 1000)
    if resp.status_code != 200:
        log.error("HTTP %s for %s", resp.status_code, url)
        resp.raise_for_status()
    if "json" not in resp.headers.get("Content-Type", ""):
        raise RuntimeError(f"non-JSON response from {url} (SPA shell?)")
    return resp.json()


def cache_dir(day: str | None = None) -> Path:
    return project_root() / "cache" / "eservices" / (day or date.today().isoformat())


def _rows(payload) -> list:
    return payload.get("data", []) if isinstance(payload, dict) else payload


def load_catalogue(refresh: bool = False, source_dir: Path | None = None) -> dict:
    """Return {'authorities': [...], 'services': [...], 'fetched_at': iso}. Cached per day."""
    d = source_dir or cache_dir()
    a_path, s_path, meta_path = d / "authorities.json", d / "services.json", d / "meta.json"
    if not refresh and a_path.exists() and s_path.exists():
        log.debug("catalogue cache hit %s", d)
        meta = load_json(meta_path) if meta_path.exists() else {}
        return {"authorities": _rows(load_json(a_path)), "services": _rows(load_json(s_path)),
                "fetched_at": meta.get("fetched_at")}
    if source_dir:
        raise FileNotFoundError(f"catalogue files missing in {source_dir}")
    log.info("fetching eServices catalogue from %s", API_BASE)
    authorities, services = get_json(AUTHORITIES_PATH), get_json(SERVICES_PATH)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dump_json(authorities, a_path)
    dump_json(services, s_path)
    dump_json({"fetched_at": fetched_at, "api_base": API_BASE}, meta_path)
    log.info("catalogue cached authorities=%d services=%d dir=%s",
             len(_rows(authorities)), len(_rows(services)), d)
    return {"authorities": _rows(authorities), "services": _rows(services), "fetched_at": fetched_at}


def fetch_detail(service_id: int, detail_dir: Path | None = None) -> dict:
    """PermitInfo + PermitLex + PermitSupportingDocuments for one service (cached)."""
    d = detail_dir or cache_dir() / "detail"
    path = d / f"{service_id}.json"
    if path.exists():
        return load_json(path)
    filt = quote(json.dumps([{"value": str(service_id), "property": "PermitTypeId"}]))
    detail = {
        "id": service_id,
        "info": get_json(INFO_PATH.format(id=service_id)),
        "lex": _rows(get_json(LEX_PATH.format(filter=filt))),
        "docs": _rows(get_json(DOCS_PATH.format(filter=filt))),
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    dump_json(detail, path)
    return detail


# ---------------------------------------------------------------- providers

def providers(catalogue: dict) -> list[dict]:
    """Every provider seen in authorities or services, with national/local service counts."""
    index: dict[str, dict] = {}
    for a in catalogue["authorities"]:
        index[a["ID"]] = {"authority_id": a["ID"], "name": a["Name"], "short_name": a.get("ShortName"),
                          "type": a.get("TypeOfAuthority_ReferenceTitle"), "national": 0, "local": 0,
                          "service_titles": set()}
    for s in catalogue["services"]:
        aid = s["AuthorityId"]
        entry = index.setdefault(aid, {"authority_id": aid, "name": s["AuthorityId_ReferenceTitle"],
                                       "short_name": s.get("ShortName"), "type": None,
                                       "national": 0, "local": 0, "service_titles": set()})
        entry["service_titles"].add(s["AuthorityId_ReferenceTitle"])
        key = "national" if "national" in (s.get("TypeOfService") or "").lower() else "local"
        entry[key] += 1
    out = []
    for e in index.values():
        e["service_titles"] = sorted(e["service_titles"])
        out.append(e)
    return out


def initials(name: str) -> str:
    """'Zambia Tourism Agency' -> 'zta' (skips 'of', 'and', 'the', '&')."""
    words = [w for w in re.split(r"[\s,&()-]+", name) if w and w.lower() not in {"of", "and", "the", "for"}]
    return "".join(w[0] for w in words).lower()


def find_agency(catalogue: dict, query: str, limit: int = 8) -> list[dict]:
    q = query.strip().lower()
    scored = []
    for p in providers(catalogue):
        names = [p["name"], p.get("short_name") or "", *p["service_titles"]]
        score = max(fuzz.token_set_ratio(q, n.lower()) for n in names if n)
        acronyms = {(p.get("short_name") or "").lower()} | {initials(n) for n in names if n}
        if q in acronyms or re.sub(r"[()]", "", q) in acronyms:
            score = max(score, 95.0)
        scored.append({**p, "score": round(score, 1)})
    scored.sort(key=lambda x: (-x["score"], x["name"]))
    log.info("find-agency %r best=%s", query, [(s["name"], s["score"]) for s in scored[:3]])
    return scored[:limit]


def agency_services(catalogue: dict, authority_ids: list[str], names: list[str],
                    include_local: bool = False) -> list[dict]:
    """Services whose AuthorityId is in authority_ids or whose provider title is in names."""
    ids = set(authority_ids)
    lowered = {n.strip().lower() for n in names}
    out = []
    for s in catalogue["services"]:
        if s["AuthorityId"] not in ids and s["AuthorityId_ReferenceTitle"].strip().lower() not in lowered:
            continue
        if not include_local and "national" not in (s.get("TypeOfService") or "").lower():
            continue
        out.append(s)
    out.sort(key=lambda s: s["ID"])
    return out


# ---------------------------------------------------------------- cleaning / mapping

def clean_text(value) -> str:
    """Unescape HTML, strip tags, normalise whitespace; 'None'/'null' -> ''."""
    if value is None:
        return ""
    text = html.unescape(str(value))
    if text.strip().lower() in {"none", "null", "n/a", "-"}:
        return ""
    text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def join_lines(text: str) -> str:
    """Multi-line API text -> one cell value separated by '; ' (a line ending in ':' joins with a space)."""
    parts = [re.sub(r"^[•\-\*·]\s*", "", p.strip()).rstrip(",;.").strip() for p in text.split("\n")]
    out = ""
    for part in (p for p in parts if p):
        if not out:
            out = part
        elif out.endswith(":"):
            out += " " + part
        else:
            out += "; " + part
    return out


def strip_boilerplate(text: str) -> str:
    keep = [ln for ln in text.split("\n") if not any(m in ln.lower() for m in BOILERPLATE_MARKERS)]
    return "\n".join(keep).strip()


def parse_who_can_apply(raw, applicant: list | None) -> str:
    names = re.findall(r"ApplicationName'?\"?\s*:\s*'?\"?([^'\",}]+)", str(raw or ""))
    names = [n.strip() for n in names if n.strip()]
    if not names and applicant:
        mapping = {"individual": "Individual", "organization": "Organization", "organisation": "Organization"}
        names = [mapping[a.lower()] for a in applicant if a.lower() in mapping]
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return "; ".join(out)


def supporting_documents(docs: list) -> list[str]:
    seen, out = set(), []
    for d in docs:
        title = clean_text(d.get("SupportingDocumentTypeId_ReferenceTitle"))
        if title and title.lower() not in seen:
            seen.add(title.lower())
            out.append(title)
    return out


def legal_references(lex: list) -> tuple[str, list[dict], list[str]]:
    """Joined names, raw entries, and review flags (non-legislation / no Act number / old Act)."""
    entries = [{"name": clean_text(x.get("Name") or x.get("Text")), "link": x.get("Link")} for x in lex]
    entries = [e for e in entries if e["name"]]
    flags = []
    for e in entries:
        if not LEGAL_HINT.search(e["name"]):
            flags.append(f"not legislation: {e['name']}")
        elif not any(p.search(e["name"]) for p in c.LEGAL_REF_PATTERNS):
            flags.append(f"missing Act/SI number: {e['name']}")
    years = [int(y) for e in entries for y in re.findall(r"\b(19\d\d|20\d\d)\b", e["name"]) if "act" in e["name"].lower()]
    if len(set(years)) > 1:
        flags.append(f"several Act years {sorted(set(years))}: check which Act is in force")
    return "; ".join(e["name"] for e in entries), entries, flags


def _source(value: str, url: str, retrieved_at: str, sha: str, evidence_path: str | None) -> list[dict]:
    src = {"url": url, "tier": 1, "retrieved_at": retrieved_at, "excerpt": value[:300], "sha256": sha}
    if evidence_path:
        src["evidence_path"] = evidence_path
    return [src]


def save_evidence(row: dict, detail: dict, evidence_dir: Path) -> tuple[str, str]:
    """Write <evidence_dir>/eservices/<ID>.json; return (path, sha256 of the file bytes)."""
    path = evidence_dir / "eservices" / f"{row['ID']}.json"
    dump_json({"service_row": row, **detail}, path)
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def to_passport(row: dict, detail: dict, ministry: str, agency: str,
                evidence_path: str | None = None, evidence_sha: str | None = None) -> dict:
    """Map one eServices service to a passport draft (verification=Pending).

    evidence_sha must be the sha256 of the saved evidence file so the verifier can check it.
    """
    info = detail.get("info") or {}
    sid = row["ID"]
    link = c.ESERVICES_SERVICE_URL.format(id=sid)
    raw_sha = evidence_sha or hashlib.sha256(json.dumps(detail, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    when = detail.get("retrieved_at") or ""
    flags: list[str] = []

    name = clean_text(info.get("Title") or row.get("Title"))
    desc = clean_text(info.get("Description") or row.get("Description"))
    if len(desc) < 40:
        extra = strip_boilerplate(clean_text(info.get("FullDescription")))
        if extra:
            desc = (desc + " " + extra).strip() if desc else extra
            flags.append("description supplemented from FullDescription")
    desc = " ".join(ln.strip() for ln in desc.split("\n") if ln.strip())

    who = parse_who_can_apply(info.get("whoCanApply"), row.get("Applicant"))
    elig_raw = clean_text(info.get("EligibilityRequirements"))
    docs = supporting_documents(detail.get("docs") or [])
    elig = join_lines(elig_raw) if elig_raw else ""
    if docs:
        elig = (elig + ". " if elig else "") + "Required documents: " + "; ".join(docs)
        flags.append("eligibility needs a 1–3 sentence summary")
    fee = join_lines(clean_text(info.get("FeeText")))
    if not fee and (row.get("Payment") or "").lower() == "fee applies":
        flags.append("fee applies but amount not published on eServices")
    ptime = join_lines(clean_text(info.get("TimeToIssueText")))
    validity = join_lines(clean_text(info.get("PeriodOfValidityText")))
    legal, lex_entries, legal_flags = legal_references(detail.get("lex") or [])
    flags.extend(legal_flags)

    values = {
        "service_name": name, "service_description": desc, "who_can_apply": who,
        "eligibility_requirements": elig, "fee": fee, "processing_time": ptime,
        "validity": validity, "legal_references": legal,
    }
    field_sources = {}
    for field, value in values.items():
        if not value:
            values[field] = c.NOT_PUBLISHED
            log.debug("service=%s field=%s empty -> %s", sid, field, c.NOT_PUBLISHED)
        field_sources[field] = _source(values[field], link, when, raw_sha, evidence_path)

    provider = row.get("AuthorityId_ReferenceTitle")
    passport = {
        "ministry": ministry, "agency": agency, **values, "source_link": link,
        "origin": "eservices", "eservices_id": sid, "reassigned_from": None, "reassignment_reason": None,
        "action": "add", "action_reason": None, "historical_fee": False,
        "field_sources": field_sources, "conflicts": [], "verification": "Pending",
        "verification_notes": None,
        "eservices_meta": {
            "provider": provider, "service_provider_text": info.get("ServiceProvider"),
            "is_available": row.get("IsAvailable"), "modified_on": info.get("ModifiedOn"),
            "external_url": row.get("ExternalUrl"), "channels": row.get("Channels"),
            "actions": row.get("Action"), "type_of_service": row.get("TypeOfService"),
            "legal_entries": lex_entries, "supporting_documents": docs,
        },
        "draft_flags": flags,
    }
    if flags:
        log.info("service=%s draft flags: %s", sid, flags)
    return passport


def to_dotgov_placeholder(row: dict, service: dict, ministry: str, agency: str, registry: dict) -> dict:
    """Placeholder passport for a DotGov-owned service: no detail fetch, no research, no verification.

    Name and description come from the DotGov registry; DOTGOV_FIELDS carry marker + token
    for a later database fill (dotgov_fill.py).
    """
    sid = row["ID"]
    link = c.ESERVICES_SERVICE_URL.format(id=sid)
    src = registry.get("source") or {}
    name = service["name"]
    desc = service.get("description") or clean_text(row.get("Description")) or c.NOT_PUBLISHED
    excerpt = f"DotGov registry {src.get('file')} ServiceID {sid}"
    source = [{"url": link, "tier": 1, "retrieved_at": src.get("extracted_at") or "", "excerpt": excerpt,
               "sha256": src.get("sha256") or ""}]
    passport = {
        "ministry": ministry, "agency": agency, "service_name": name, "service_description": desc,
        **{f: dotgov_registry.placeholder(service, f) for f in c.DOTGOV_FIELDS},
        "source_link": link, "origin": "eservices", "eservices_id": sid,
        "reassigned_from": None, "reassignment_reason": None,
        "action": "add", "action_reason": None, "historical_fee": False,
        "field_sources": {"service_name": source, "service_description": source},
        "conflicts": [], "verification": c.DOTGOV_VERIFICATION,
        "verification_notes": "DotGov service: not researched; fields filled later from the DotGov database",
        "dotgov": {"service_id": sid, "agency": service["agency"], "service_name": name, "match": "id",
                   "registry_sha256": src.get("sha256")},
        "eservices_meta": {"provider": row.get("AuthorityId_ReferenceTitle"), "is_available": row.get("IsAvailable"),
                           "type_of_service": row.get("TypeOfService")},
    }
    log.debug("dotgov skip %s", kv(id=sid, name=name, dotgov_agency=service["agency"], agency=agency))
    return passport


# ---------------------------------------------------------------- commands

def build_agency(catalogue: dict, authority_ids: list[str], names: list[str], ministry: str, agency: str,
                 include_local: bool, evidence_dir: Path | None, detail_dir: Path | None = None,
                 dotgov: dict | None = None) -> dict:
    """Exact eServices count + passports. Services in the DotGov registry become placeholders
    (still counted in service_ids/service_count); only the others are fetched as drafts."""
    rows = agency_services(catalogue, authority_ids, names, include_local)
    owned = dotgov_registry.by_id(dotgov)
    log.info("agency=%r authority_ids=%s names=%s services=%d dotgov=%d", agency, authority_ids, names, len(rows),
             sum(1 for r in rows if r["ID"] in owned))

    def work(row):
        if row["ID"] in owned:
            return to_dotgov_placeholder(row, owned[row["ID"]], ministry, agency, dotgov)
        detail = fetch_detail(row["ID"], detail_dir)
        ev_path = ev_sha = None
        if evidence_dir:
            ev_path, ev_sha = save_evidence(row, detail, evidence_dir)
        return to_passport(row, detail, ministry, agency, ev_path, ev_sha)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        passports = list(pool.map(work, rows))
    provider_names = sorted({r["AuthorityId_ReferenceTitle"] for r in rows})
    return {
        "eservices": {
            "status": c.ESERVICES_LISTED if rows else c.ESERVICES_NOT_LISTED,
            "eservices_name": "; ".join(provider_names) or None,
            "authority_ids": sorted({r["AuthorityId"] for r in rows} | set(authority_ids)),
            "service_count": len(rows),
            "service_ids": [r["ID"] for r in rows],
            "excluded": [], "reassigned_out": [],
        },
        "catalogue_fetched_at": catalogue.get("fetched_at"),
        "passports": passports,
    }


def _emit(data, out: Path | None) -> None:
    if out:
        dump_json(data, out)
        log.info("wrote %s", out)
    else:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("catalogue")
    cp.add_argument("--refresh", action="store_true")
    fp = sub.add_parser("find-agency")
    fp.add_argument("query")
    fp.add_argument("--limit", type=int, default=8)
    ag = sub.add_parser("agency")
    ag.add_argument("--authority-id", action="append", default=[])
    ag.add_argument("--name", action="append", default=[], help="exact eServices provider title")
    ag.add_argument("--ministry", required=True)
    ag.add_argument("--agency", required=True, help="official agency name for the passports")
    ag.add_argument("--include-local", action="store_true")
    ag.add_argument("--evidence", type=Path)
    ag.add_argument("--out", type=Path)
    ag.add_argument("--no-dotgov", action="store_true", help="draft DotGov services too (no placeholders)")
    sp = sub.add_parser("service")
    sp.add_argument("id", type=int)
    sp.add_argument("--evidence", type=Path)
    args = ap.parse_args()

    try:
        if args.cmd == "catalogue":
            cat = load_catalogue(refresh=args.refresh)
            national = sum(1 for s in cat["services"] if "national" in (s.get("TypeOfService") or "").lower())
            _emit({"authorities": len(cat["authorities"]), "services": len(cat["services"]),
                   "national_services": national, "fetched_at": cat["fetched_at"],
                   "cache_dir": str(cache_dir())}, None)
        elif args.cmd == "find-agency":
            _emit(find_agency(load_catalogue(), args.query, args.limit), None)
        elif args.cmd == "agency":
            if not args.authority_id and not args.name:
                log.error("give --authority-id and/or --name")
                return 2
            data = build_agency(load_catalogue(), args.authority_id, args.name, args.ministry, args.agency,
                                args.include_local, args.evidence,
                                dotgov=None if args.no_dotgov else dotgov_registry.load())
            _emit(data, args.out)
        elif args.cmd == "service":
            detail = fetch_detail(args.id)
            if args.evidence:
                dump_json(detail, args.evidence / "eservices" / f"{args.id}.json")
            _emit(detail, None)
    except requests.RequestException as exc:
        log.error("network error: %s", exc)
        return 3
    except RuntimeError as exc:
        log.error("%s", exc)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
