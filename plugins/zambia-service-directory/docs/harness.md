# Zambia Service Directory Harness

A set of Claude Code skills, agents and Python scripts. For **one ministry at a time**, it researches every agency's public services,
verifies them, and writes a workbook named `Zambia_National_Service_Directory_<Ministry_Name>.xlsx`.
Every ministry workbook comes out in the same fixed format.

## Architecture

```
/zm-ministry-directory "<Ministry>"
  │
  ├─ run_state.py init ─────────► output/<Ministry>/runs/<ts>/run.json, progress.json   (+ eServices catalogue cache)
  ├─ agent zm-roster-builder ───► roster.json   (agencies, entity types, eServices provider IDs, reassigned services)
  ├─ per agency (≤4 in parallel)
  │    agency_file.py init ─────► agencies/<slug>.json   (eServices drafts + previous rows)
  │    agent zm-agency-researcher► same file, completed; evidence/<slug>/ ; logs/<slug>.md
  │    agency_file.py check ────► rule check (validate_passports.py)
  │    agent zm-passport-verifier► verify/<slug>.json   (fresh context, re-reads evidence + live sources)
  │    agency_file.py apply-verdict ─► Verified | Verified with limitations | repair → Unresolved
  ├─ build_workbook.py ─────────► the workbook (+ versions/<name>_vNNN_<date>.xlsx), build.json
  ├─ validate_workbook.py ──────► workbook_validation.json
  ├─ /zm-verify-ministry ───────► audit.json, audit.md   (PASS/FAIL, eServices drift)
  └─ report.py ─────────────────► report.md
```

| Piece | File |
|---|---|
| Orchestrator skill | `${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/SKILL.md` |
| Audit skill | `${CLAUDE_PLUGIN_ROOT}/skills/zm-verify-ministry/SKILL.md` |
| Agents | `${CLAUDE_PLUGIN_ROOT}/agents/zm-roster-builder.md`, `zm-agency-researcher.md`, `zm-passport-verifier.md` |
| Rules for agents | `references/TERMINOLOGY.md` (fixed strings, counting, scope), `references/SOURCES.md` (tiers, search recipes), `references/ESERVICES-API.md`, `references/ROLES.md` (no-subagent fallback) |
| Data contracts | `references/schemas/{roster,agency,passport,ministries}.schema.json`, `scripts/zmcontract.py` |
| Format | `templates/reference_workbook.xlsx` → `templates/workbook_spec.json` |
| Ministry numbers | `input/ministries.json` |

## Data sources

1. **Zambia eServices.** A public JSON API (`https://zigsapi.eservices.gov.zm/`) sits behind the Angular directory. `eservices.py` reads
   providers, services and per-service detail: fees, time, validity, legal references and required documents. It also maps them to passports.
2. **Official sources** (tiers 2–4): agency sites, forms, fee schedules, Acts/SIs (ZambiaLII, parliament.gov.zm), and ministry and regulator pages.
3. **Leads** (tier 5): LinkedIn, news and Wikipedia. These are only used to find official pages. They never back a value on their own.

Every cited page is saved by `fetch_source.py`: raw file, text and sha256. The verifier re-reads the exact copy.

## Terminology and counting

See `references/TERMINOLOGY.md`. Key points:
- Missing values are written as `Not published`. The old `Not specified` is rewritten only when the row is re-checked.
- One eServices entry = one passport. Payment and submission channels are never separate passports.
- A service listed under one eServices provider but delivered by another agency is decided in the roster and counted once, under the delivering agency.
- Totals are always computed from the written rows.

## Models

Pinned in the frontmatter (`model:`) of each file; change them there.

| File | Model | Why |
|---|---|---|
| `zm-ministry-directory`, `zm-verify-ministry` (orchestrators) | `claude-sonnet-5-5` | script running and routing |
| `zambia-service-directory:zm-roster-builder`, `zambia-service-directory:zm-agency-researcher` | `claude-sonnet-5-5` | bulk research; the verifier catches its mistakes |
| `zambia-service-directory:zm-passport-verifier` (also used by the audit) | `claude-opus-5-5` | judgment work, about 63% of tokens; a different model from the researcher |

Verification is the main cost. Before moving the verifier to Sonnet, compare its verdicts to Opus on one agency.

## Profiles and speed levers

`--profile fast|balanced|thorough` (default from `input/settings.json`) sets models, parallelism and verification depth; `--set key=value` overrides one setting.
The resolved settings are stored in `runs/<ts>/run.json`, so `--resume` keeps them. Full description: [USER-GUIDE.md](USER-GUIDE.md).

| Lever | Script | Effect |
|---|---|---|
| Script check of eServices values | `agency_file.py live-check` | fresh API re-fetch and compare; the verifier skips passports marked `ok` |
| Re-verify only changed passports | `agency_file.py plan-verify --scope pending` | passports already Verified are not re-checked after a repair |
| Parallel verification of big agencies | `plan-verify --chunk N` + `merge-verdicts` | shortens the slowest step |
| Audit focus | `audit.py prepare --focus researcher_written` | samples rows not copied straight from eServices first |

Measured (two small tests, seeded errors, n = 2 agencies, one run each; not a benchmark):

| Verifier | Tokens / clock | Planted errors caught |
|---|---|---|
| Sonnet 5.5 (whole agency) | 37k and 33k tokens; 62 s and 56 s | 1 of 3 (casino-law omission and invented applicants missed) |
| Opus 5.5 (Tourism, 2 parallel parts, script live-check) | 137k tokens; 225 s | flagged the omission on 5 of 7 passports, but as "limits", not a failure |
| Opus 5.5 (Tourism, single agent, earlier real run) | 102k tokens; 354 s | failed all 7 on the omission |

Findings: (1) both models judge an omitted governing Act inconsistently, so the verifier rule now says an omitted governing or saved Act is a FAIL; (2) splitting into parallel parts cut the clock by about a third but cost about 35% more tokens; (3) the Opus round-1 and round-2 verifiers in the real run missed the craft-shop applicants error, which only the audit caught. Token and clock estimates for the profiles are extrapolated, not measured end to end.

## Ministry number

`Ministry No.` comes from `input/ministries.json`. You may replace that file with your own list (`[{ministry_no, name, acronym?, website?}]`).
A ministry not in the list gets the next free number (highest + 1), and the number is saved there, so it never changes.


## Resume

A run interrupted at any point continues with `/zm-ministry-directory "<Ministry>" --resume output/<Ministry>/runs/<ts>`.
`progress.json` records each agency's state: `pending → researched → repair → verified | unresolved → done`.

## Logging

`LOG_LEVEL=DEBUG` gives verbose script logs (the default is INFO). `ZM_RUN_DIR=<run>` also appends them to `<run>/run.log`.


Cowork and chat Projects are not targets. With no subagents, the orchestrator plays the roles itself (see `references/ROLES.md`).

## First live run: Ministry of Tourism (2026-09-29)

Starting point: the 89-row example workbook. Result: **79 passports, 7 included agencies** (`output/Ministry_of_Tourism/`; report in `runs/20260929-124334/report.md`).

| Change | Count |
|---|---|
| Added (HBOM eServices service ID 505; 2 HMRC services from SI 94 of 2016) | 3 |
| Corrected | 68 |
| Removed (information-only look-ups, duplicates, record forms, statutory duties) | 13 |
| Kept without re-check because no official source was found | 8 (3 HBOM, 5 NHCC) |

Findings that changed the harness: partial repeals need a savings-clause check (casino Part VI of the 2007 Act); agency renames need slug and roster-name matching; a scanned fee schedule needs a rendered-page check. Audit: 12 sampled rows, 2 failed (NMB Craft Shop applicants; NHCC "by site category" wording). Both were fixed afterwards and not re-audited. Verdict **FAIL**: the 8 kept rows still carry `Not specified`, which fails workbook validation until a later run re-checks them.

## Known limitations

- **No ministry field.** eServices has no ministry field, so the ministry-to-agency mapping depends on the roster agent's research.
- **LinkedIn.** Needs a login for details, so it gives search-snippet leads only.
- **Broken TLS.** Many `.gov.zm` sites have broken TLS chains. `fetch_source.py` retries unverified and records `tls_verified: false`.
- **JavaScript pages.** These need Chrome DevTools MCP (`.mcp.json`). Without it they are recorded as a source limitation.
- **Carried-over rows.** Rows from an older workbook that a run neither confirms nor removes are kept unchanged and reported as carried over. They may still fail workbook validation (e.g. `Not specified`) until a later run re-checks them.
- **Faithful copies.** Reference-workbook quirks that are not rules, such as the Carlito default font of empty cells, are copied as they are.
