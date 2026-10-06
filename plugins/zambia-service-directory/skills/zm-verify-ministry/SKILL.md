---
name: zm-verify-ministry
description: Audit a Zambia ministry service-directory workbook for format, totals, placeholders, duplicates, source accuracy and eServices drift; writes audit.md with PASS/FAIL. Use after /zambia-service-directory:zm-ministry-directory, or on any ministry workbook edited by hand.
model: claude-sonnet-5-5
argument-hint: '<workbook.xlsx> [--run <RUN>] [--sample N] [--focus researcher_written|all] [--verifier-model M]'
---

# Verify a Ministry Workbook

Independent check of one `Zambia_National_Service_Directory_<Ministry>.xlsx`. It never edits the workbook.

```bash
export ZM_ROOT="$(pwd)"; bash "${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/scripts/bootstrap.sh" --venv "${CLAUDE_PLUGIN_DATA}/venv"
PY="${CLAUDE_PLUGIN_DATA}/venv/bin/python"; [ -x "$PY" ] || PY="${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe"; S="${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/scripts"
```

## Intake (only when the workbook path is missing)
If no workbook path was given, use **AskUserQuestion**: find `output/*/Zambia_National_Service_Directory_*.xlsx` (Glob), and offer up to 3 of the newest as options
("Which workbook should I audit?"), plus the built-in "Other" for typing a path. Do not ask about `--sample`, `--focus` or `--verifier-model`; use the defaults.
If AskUserQuestion is unavailable or no workbook exists, stop and print the usage line.

## Arguments
- `$ARGUMENTS[0]`: workbook path.
- `--run <RUN>`: the run folder that produced it. Without it, the skill creates `output/<Ministry>/audits/<timestamp>/` as `AUD`.
- `--sample N`: how many rows to re-verify at random (default 12).
- `--focus researcher_written|all`: `researcher_written` (default) samples rows that did not come straight from eServices first, because they carry the judgement calls; `all` samples every row uniformly.
- `--verifier-model M`: model for the verifier agents (default `claude-opus-5-5`).

Set `OUT` = RUN if given, else AUD.

**DotGov placeholders are expected, not failures.** Cells such as `FROM DOTGOV [Department of Tourism - Casino Licence] {{DOTGOV:96:fee}}`
mark services DotGov built; their data is filled later from the DotGov database. They are counted (`dotgov_rows`, `dotgov_placeholders`),
never sampled for re-verification, and only a malformed token (`W-DOTGOV`, `R-DOTGOV`) is an error.

## Steps

1. **Format and totals.** `"$PY" -B "$S/validate_workbook.py" <workbook> --out OUT/workbook_audit.json --summary`
   This checks sheet order, headers, section layout, merges, styles, totals vs rows, placeholders, duplicates and formula errors.

2. **Passport rules.** `"$PY" -B "$S/audit.py" prepare <workbook> --out OUT [--run RUN] [--sample N] [--focus F] [--chunk N]`
   - It rebuilds per-agency files from the sheet rows (or uses `RUN/agencies`).
   - It runs `validate_passports.py` and re-counts eServices services against a fresh catalogue (drift).
   - It picks a random sample of `N` written rows to re-verify (with `--run`, rows changed by that run are not forced in, because the run's own verifier already checked them; use a large `--sample` for a full re-check).
   - With `--run` the slugs are the run's own. A slug with more sampled rows than `--chunk` (default 6) is split: it appears in `parts[slug]` as `{part, indices}`.
   Output: `OUT/audit_prepare.json`, with `sample[]` grouped by slug, `parts` for split slugs, and one file per slug under `OUT/audit/agencies/`.

3. **Source re-check.** For each slug in `sample`, start the `zambia-service-directory:zm-passport-verifier` agent (`model` = `"$PY" -B "$S/settings.py" alias <--verifier-model>`, i.e. `opus` or `sonnet`; never omit it, the agent file's own model would apply) with
   `RUN=OUT/audit` and `SLUG=<slug>`. Launch up to 4 at once.
   For a slug listed in `parts`, start **one verifier per part**, each with `PART` and `INDICES` from that entry (a verifier that gets
   too many rows runs out of turns before it writes its verdict). `audit.py finish` merges the part files; a missing part counts as a missing verdict.
   Rows that came from the sheet without saved evidence are checked live (WebFetch / `eservices.py service <ID>`).

4. **Verdict.** `"$PY" -B "$S/audit.py" finish --out OUT` → writes `OUT/audit.json` and `OUT/audit.md`:
   - **PASS**: no workbook errors, no passport-rule errors, no FAIL in the sample, no eServices drift, and a complete verdict for every sampled row.
   - **FAIL**: otherwise. The report has a table of failing rows (sheet!cell, rule, message, fix) and the drift list
     (services added or removed on eServices since the workbook was built).

## Final message
`<workbook>: audit PASS|FAIL — W workbook errors, P passport errors, S/N sampled rows failed, D eServices drift, G DotGov placeholders`,
plus the path of `audit.md`.
