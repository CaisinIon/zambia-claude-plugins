#!/usr/bin/env python3
"""Fill DotGov placeholders in a ministry workbook from a DotGov database export.

Usage:
  dotgov_fill.py fill <workbook.xlsx> --data <export.json|export.csv> [--out FILE] [--report FILE]
  dotgov_fill.py batch <folder> --data <export.json|export.csv> --out-dir DIR [--report FILE] [--date YYYY-MM-DD] [--force]

Every Service Passport cell holding `FROM DOTGOV [...] {{DOTGOV:<ServiceID>:<field>}}` is replaced, as a
whole cell, by the export's value for (ServiceID, field). A missing or empty value keeps the cell and is
listed under `missing`. Styles are kept (values only are changed).

Export: one row per service, keyed by `ServiceID` (or `service_id`). Field columns use the passport field
keys (who_can_apply, eligibility_requirements, fee, processing_time, validity, legal_references) or the
workbook headers (Who Can Apply, Fee, ...). CSV must be UTF-8; JSON is a list of objects or {"services": [...]}.

Default --out: <workbook>_filled.xlsx next to the input. The input is overwritten only when --out names it.
The result is checked with validate_workbook.py; exit 0 = filled and valid, 1 = workbook errors, 2 = bad input.

batch: every *.xlsx in <folder> goes to --out-dir under the same name. A workbook with no DotGov token is copied
byte for byte; one with tokens is filled, and when all its tokens were filled the Agencies Notes suffix
"; N from DotGov (data to be filled)" becomes "; N from DotGov eServices (filled from the DotGov database on DATE)".
The source folder is never changed. --out-dir must be new (or --force) and must not be inside <folder>.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter

import dotgov_registry
import zmcontract as c
from zmlog import dump_json, get_logger, kv, load_json, templates_dir
from zmstyle import estimate_row_height

log = get_logger("dotgov_fill")
ID_KEYS = ("ServiceID", "service_id", "serviceid", "ID")
HEADER_TO_FIELD = {h.lower(): f for f, h in c.PASSPORT_COLUMNS}


def _field_key(key: str) -> str | None:
    k = str(key or "").strip()
    if k in c.DOTGOV_FIELDS:
        return k
    f = HEADER_TO_FIELD.get(k.lower())
    return f if f in c.DOTGOV_FIELDS else None


def load_export(path: Path) -> dict[int, dict[str, str]]:
    """{ServiceID: {field: value}} from a CSV or JSON export. Unknown columns are ignored."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    elif path.suffix.lower() == ".json":
        data = load_json(path)
        rows = data.get("services", []) if isinstance(data, dict) else data
    else:
        raise ValueError(f"{path.name}: export must be .csv or .json")
    out: dict[int, dict[str, str]] = {}
    for n, row in enumerate(rows, start=1):
        raw = next((row[k] for k in ID_KEYS if k in row and row[k] not in (None, "")), None)
        try:
            sid = int(float(raw))
        except (TypeError, ValueError):
            raise ValueError(f"{path.name} row {n}: no integer ServiceID ({raw!r})") from None
        values = {}
        for k, v in row.items():
            f = _field_key(k)
            if f and v is not None and str(v).strip():
                values[f] = str(v).strip()
        if sid in out:
            log.warning("export row %d repeats ServiceID %d; later values win", n, sid)
        out.setdefault(sid, {}).update(values)
    log.info("export loaded %s", kv(file=str(path), services=len(out)))
    return out


def fit_row_heights(ws, rows: set[int], min_height: float = 15.0) -> int:
    """Grow each filled row so its wrapped text shows (same estimate as build_workbook). Never shrinks a row."""
    widths = [ws.column_dimensions[get_column_letter(i)].width for i in range(1, ws.max_column + 1)]
    grown = 0
    for r in sorted(rows):
        cells = [ws.cell(row=r, column=i) for i in range(1, ws.max_column + 1)]
        font_size = next((float(x.font.sz) for x in cells if x.font is not None and x.font.sz), 9.0)
        current = ws.row_dimensions[r].height
        height = estimate_row_height([x.value for x in cells], widths, max(min_height, current or 0), font_size)
        if current is None or height > current:
            ws.row_dimensions[r].height = height
            grown += 1
            log.debug("row %d height %s -> %s", r, current, height)
    return grown


def fill(workbook: Path, export: dict[int, dict[str, str]], out: Path, warn_unknown: bool = True) -> dict:
    wb = openpyxl.load_workbook(workbook)
    if "Service Passport" not in wb.sheetnames:
        raise ValueError(f"{workbook.name}: no 'Service Passport' sheet")
    ws = wb["Service Passport"]
    filled, missing, skipped, seen, rows_changed = 0, [], [], set(), set()
    for row in ws.iter_rows():
        for cell in row:
            if not isinstance(cell.value, str) or "DOTGOV" not in cell.value:
                continue
            parsed = dotgov_registry.parse_cell(cell.value)
            if not parsed:
                log.warning("%s: not a well-formed DotGov placeholder; left as is: %r", cell.coordinate, cell.value)
                skipped.append({"cell": cell.coordinate, "value": cell.value})
                continue
            sid, field = parsed["service_id"], parsed["field"]
            seen.add(sid)
            value = (export.get(sid) or {}).get(field)
            if not value:
                missing.append({"cell": cell.coordinate, "service_id": sid, "field": field})
                log.debug("%s %s:%s no value in export", cell.coordinate, sid, field)
                continue
            if isinstance(cell, MergedCell):
                skipped.append({"cell": cell.coordinate, "value": cell.value, "reason": "merged cell"})
                continue
            cell.value = value
            filled += 1
            rows_changed.add(cell.row)
            log.debug("%s %s:%s filled", cell.coordinate, sid, field)
    grown = fit_row_heights(ws, rows_changed)
    unknown = sorted(set(export) - seen)
    if unknown and warn_unknown:
        log.warning("export ServiceIDs not in this workbook: %s", unknown[:20])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    result = {"workbook": str(workbook), "out": str(out), "filled": filled, "missing": missing,
              "missing_count": len(missing), "unknown_ids": unknown, "skipped": skipped,
              "services_in_workbook": len(seen), "rows_grown": grown}
    log.info("fill %s", kv(services=len(seen), filled=filled, rows_grown=grown, missing=len(missing), unknown_ids=len(unknown),
                            skipped=len(skipped), out=str(out)))
    return result


NOTES_PENDING_RE = re.compile(re.escape(c.NOTES_DOTGOV_SUFFIX).replace(re.escape("{dotgov}"), r"(\d+)"))


def rewrite_notes(workbook: Path, fill_date: str) -> int:
    """Agencies Notes: pending DotGov suffix -> filled suffix. Returns the number of cells changed."""
    wb = openpyxl.load_workbook(workbook)
    if "Agencies" not in wb.sheetnames:
        log.warning("%s: no 'Agencies' sheet; Notes not rewritten", Path(workbook).name)
        return 0
    ws = wb["Agencies"]
    header = next(((cell.row, cell.column) for row in ws.iter_rows() for cell in row if cell.value == "Notes"), None)
    if header is None:
        log.warning("%s: no 'Notes' header on Agencies; Notes not rewritten", Path(workbook).name)
        return 0
    changed = 0
    for r in range(header[0] + 1, ws.max_row + 1):
        cell = ws.cell(row=r, column=header[1])
        if not isinstance(cell.value, str) or isinstance(cell, MergedCell):
            continue
        new = NOTES_PENDING_RE.sub(lambda m: c.NOTES_DOTGOV_FILLED_SUFFIX.format(dotgov=m.group(1), date=fill_date),
                                   cell.value)
        if new != cell.value:
            log.debug("%s %s notes %r -> %r", Path(workbook).name, cell.coordinate, cell.value, new)
            cell.value = new
            changed += 1
    if changed:
        wb.save(workbook)
    return changed


def _has_token(workbook: Path) -> bool:
    wb = openpyxl.load_workbook(workbook, read_only=True)
    try:
        return any(isinstance(v, str) and "{{DOTGOV:" in v
                   for ws in wb.worksheets for row in ws.iter_rows(values_only=True) for v in row)
    finally:
        wb.close()


def batch(src_dir: Path, export: dict[int, dict[str, str]], out_dir: Path, fill_date: str,
          force: bool = False) -> dict:
    """Fill every workbook of src_dir into out_dir (same names). The source folder is never written."""
    src, out = Path(src_dir).resolve(), Path(out_dir).resolve()
    if not src.is_dir():
        raise ValueError(f"{src_dir}: not a folder")
    if out == src or src in out.parents:
        raise ValueError(f"--out-dir must not be the source folder or inside it: {out_dir}")
    if out.exists() and not force:
        raise ValueError(f"--out-dir already exists (use --force to write into it): {out_dir}")
    out.mkdir(parents=True, exist_ok=True)
    files, seen = [], set()
    for wb_path in sorted(p for p in src.glob("*.xlsx") if not p.name.startswith(("~$", "."))):
        target = out / wb_path.name
        if not _has_token(wb_path):
            shutil.copy2(wb_path, target)
            files.append({"file": wb_path.name, "action": "copied", "filled": 0, "missing_count": 0})
            log.info("batch %s", kv(file=wb_path.name, action="copied"))
            continue
        r = fill(wb_path, export, target, warn_unknown=False)
        seen.update(set(export) - set(r["unknown_ids"]))       # export IDs present in this workbook
        notes = 0
        if r["missing_count"] == 0 and not r["skipped"]:
            notes = rewrite_notes(target, fill_date)
        else:
            log.warning("batch %s", kv(file=wb_path.name, notes="not rewritten (placeholders left)",
                                       missing=r["missing_count"], skipped=len(r["skipped"])))
        files.append({"file": wb_path.name, "action": "filled", "filled": r["filled"],
                      "missing_count": r["missing_count"], "missing": r["missing"], "skipped": r["skipped"],
                      "services": r["services_in_workbook"], "rows_grown": r["rows_grown"], "notes_rewritten": notes})
        log.info("batch %s", kv(file=wb_path.name, action="filled", filled=r["filled"], missing=r["missing_count"],
                                notes_rewritten=notes))
    unused = sorted(set(export) - seen)
    totals = {"files": len(files), "filled_files": sum(f["action"] == "filled" for f in files),
              "copied_files": sum(f["action"] == "copied" for f in files),
              "filled": sum(f["filled"] for f in files), "missing": sum(f["missing_count"] for f in files),
              "export_ids_not_in_any_workbook": len(unused)}
    log.info("batch done %s", kv(out_dir=str(out), **totals))
    return {"src_dir": str(src), "out_dir": str(out), "date": fill_date, "totals": totals,
            "export_ids_not_in_any_workbook": unused, "files": files}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("fill")
    fp.add_argument("workbook", type=Path)
    fp.add_argument("--data", type=Path, required=True)
    fp.add_argument("--out", type=Path)
    fp.add_argument("--report", type=Path)
    bp = sub.add_parser("batch")
    bp.add_argument("folder", type=Path)
    bp.add_argument("--data", type=Path, required=True)
    bp.add_argument("--out-dir", type=Path, required=True)
    bp.add_argument("--report", type=Path)
    bp.add_argument("--date", default=date.today().isoformat(), help="date written in the filled Notes suffix")
    bp.add_argument("--force", action="store_true", help="write into an existing --out-dir")
    args = ap.parse_args()
    from validate_workbook import validate_workbook

    if args.cmd == "batch":
        try:
            result = batch(args.folder, load_export(args.data), args.out_dir, args.date, args.force)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.error("%s", exc)
            return 2
        spec = load_json(templates_dir() / "workbook_spec.json")
        errors = 0
        for f in result["files"]:
            check = validate_workbook(Path(result["out_dir"]) / f["file"], spec)
            f["validation"] = {k: v for k, v in check.items() if k != "findings"}
            errors += check["errors"]
        result["totals"]["validation_errors"] = errors
        if args.report:
            dump_json(result, args.report)
        print(json.dumps({"out_dir": result["out_dir"], **result["totals"]}, ensure_ascii=False, indent=2))
        return 0 if errors == 0 else 1

    out = args.out or args.workbook.with_name(f"{args.workbook.stem}_filled.xlsx")
    try:
        result = fill(args.workbook, load_export(args.data), out)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.error("%s", exc)
        return 2
    check = validate_workbook(out, load_json(templates_dir() / "workbook_spec.json"))
    result["validation"] = {k: v for k, v in check.items() if k != "findings"}
    if args.report:
        dump_json(result, args.report)
    print(json.dumps({k: v for k, v in result.items() if k not in ("missing", "skipped")}, ensure_ascii=False, indent=2))
    return 0 if check["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
