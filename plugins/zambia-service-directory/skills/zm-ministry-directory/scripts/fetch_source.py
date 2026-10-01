#!/usr/bin/env python3
"""Fetch an official source (web page or PDF) and save it as evidence.

Usage: fetch_source.py <url> --out <run>/evidence [--tier N] [--note "..."]

Saves under <out>/sources/<sha8>-<slug>/: raw file, text.txt, meta.json.
Prints meta JSON (includes text_path) on stdout; read or grep text.txt afterwards.

Exit codes: 0 ok · 3 JavaScript-rendered page (use chromeDevtools MCP:
navigate_page + take_snapshot, then save the snapshot text with --from-text) ·
4 HTTP/network error · 5 empty document.

--from-text FILE  store text captured another way (e.g. a browser snapshot) as evidence for <url>.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from pypdf import PdfReader

from zmlog import dump_json, get_logger

log = get_logger("fetch_source")

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 45
MIN_TEXT_CHARS = 500
SPA_MARKERS = ("<app-root", "ng-version", "id=\"root\"></div>", "id=\"__next\"", "enable javascript",
               "you need to enable javascript")


def slugify(url: str) -> str:
    p = urlparse(url)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (p.netloc + p.path + ("-" + p.query if p.query else "")))
    return s.strip("-").lower()[:40].strip("-") or "source"  # short: Windows paths stop at 260 characters


def html_to_text(content: bytes, base_url: str = "") -> str:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    lines = []
    for a in soup.find_all("a", href=True):
        # keep link targets visible so agents can follow form/PDF links
        a.append(f" <{urljoin(base_url, a['href'])}>")
    for line in soup.get_text("\n").split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def pdf_to_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        pages.append(f"--- page {i} ---\n{text.strip()}")
    return "\n".join(pages)


def is_spa_shell(raw: bytes, text: str) -> bool:
    head = raw[:1_000_000].decode("utf-8", "ignore").lower()
    return len(text) < MIN_TEXT_CHARS and any(m in head for m in SPA_MARKERS)


def extract_text(raw: bytes, content_type: str, url: str) -> tuple[str, str]:
    """Return (text, kind) where kind is 'pdf', 'html' or 'text'."""
    ct = (content_type or "").lower()
    if "pdf" in ct or raw[:5] == b"%PDF-" or url.lower().endswith(".pdf"):
        return pdf_to_text(raw), "pdf"
    if "html" in ct or b"<html" in raw[:2000].lower():
        return html_to_text(raw, url), "html"
    return raw.decode("utf-8", "ignore"), "text"


def download(url: str) -> tuple[requests.Response, bool]:
    """GET with a browser UA; if TLS verification fails, retry unverified and report it."""
    headers = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/pdf,*/*;q=0.8"}
    try:
        return requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True), True
    except requests.exceptions.SSLError as exc:
        log.warning("TLS verification failed for %s (%s); retrying without verification", url, exc)
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True, verify=False), False


def save(url: str, out: Path, raw: bytes, text: str, kind: str, meta_extra: dict) -> dict:
    sha = hashlib.sha256(raw).hexdigest()
    folder = out / "sources" / f"{sha[:8]}-{slugify(url)}"
    folder.mkdir(parents=True, exist_ok=True)
    ext = {"pdf": ".pdf", "html": ".html"}.get(kind, ".txt")
    raw_path = folder / f"raw{ext}"
    raw_path.write_bytes(raw)
    text_path = folder / "text.txt"
    text_path.write_text(text, encoding="utf-8")
    meta = {"url": url, "sha256": sha, "kind": kind, "chars": len(text),
            "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "raw_path": str(raw_path), "text_path": str(text_path), **meta_extra}
    dump_json(meta, folder / "meta.json")
    log.info("saved url=%s kind=%s bytes=%d chars=%d sha=%s", url, kind, len(raw), len(text), sha[:12])
    return meta


def fetch(url: str, out: Path, tier: int | None = None, note: str | None = None) -> tuple[int, dict]:
    log.debug("fetch start url=%s", url)
    try:
        resp, tls_ok = download(url)
    except requests.RequestException as exc:
        log.error("network error url=%s error=%s", url, exc)
        return 4, {"url": url, "error": str(exc)}
    if resp.status_code >= 400:
        log.error("HTTP %s url=%s", resp.status_code, url)
        return 4, {"url": url, "status": resp.status_code, "error": f"HTTP {resp.status_code}"}
    raw = resp.content
    text, kind = extract_text(raw, resp.headers.get("Content-Type", ""), resp.url)
    extra = {"final_url": resp.url, "status": resp.status_code,
             "content_type": resp.headers.get("Content-Type"), "tls_verified": tls_ok,
             "tier": tier, "note": note}
    if kind == "html" and is_spa_shell(raw, text):
        meta = save(url, out, raw, text, kind, {**extra, "spa": True})
        log.warning("JS-rendered page url=%s; use chromeDevtools MCP then --from-text", url)
        return 3, meta
    if not text.strip():
        log.warning("empty text url=%s kind=%s (scanned PDF?)", url, kind)
        return 5, save(url, out, raw, text, kind, {**extra, "empty": True})
    return 0, save(url, out, raw, text, kind, extra)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", type=Path, required=True, help="evidence directory of the run")
    ap.add_argument("--tier", type=int, choices=range(1, 6))
    ap.add_argument("--note")
    ap.add_argument("--from-text", type=Path, help="store this text file as the evidence for url")
    args = ap.parse_args()
    if args.from_text:
        text = args.from_text.read_text(encoding="utf-8")
        meta = save(args.url, args.out, text.encode("utf-8"), text, "snapshot",
                    {"final_url": args.url, "status": None, "tier": args.tier, "note": args.note,
                     "captured_with": "browser snapshot"})
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        return 0
    code, meta = fetch(args.url, args.out, args.tier, args.note)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
