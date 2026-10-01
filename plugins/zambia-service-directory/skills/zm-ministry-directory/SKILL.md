---
name: zm-ministry-directory
description: Research one Zambian ministry's agencies and public services (eServices + official sources), verify every service passport, and write the ministry's National Service Directory Excel workbook. Use when asked to build, update or research a ministry directory or service passports for Zambia.
model: claude-sonnet-5-5
argument-hint: '"<Ministry Name>" [--profile fast|balanced|thorough] [--set key=value] [--import <xlsx>] [--include-local] [--resume <RUN>] [--ministry-no N]'
---

# Zambia Ministry Service Directory

Build or update `Zambia_National_Service_Directory_<Ministry_Name>.xlsx` for **exactly one ministry** per run.
After the short intake questions (Step 0), the run is autonomous: no confirmation stops. Only verified passports are written.

## Setup (every run)

Work from the project root (the folder containing `input/` and `requirements.txt`).
```bash
export ZM_ROOT="$(pwd)"; bash "${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/scripts/bootstrap.sh" --venv "${CLAUDE_PLUGIN_DATA}/venv"   # creates .venv once; last line = python path
PY="${CLAUDE_PLUGIN_DATA}/venv/bin/python"; [ -x "$PY" ] || PY="${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe"; S="${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/scripts"; R="${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/references"
```
Same commands on macOS, Linux and Windows (Claude Code runs them in Git Bash there).
**Quote every path** (`"$PY" -B "$S/x.py" "RUN"`): Windows folders often contain spaces. Contract and rules: `$R/TERMINOLOGY.md`, `$R/SOURCES.md`, `$R/ESERVICES-API.md`.

## Step 0. Intake (ask with pop-ups; only for what is missing)
Customers do not know the flags. Before Step 1, use the **AskUserQuestion** tool to fill in whatever `$ARGUMENTS` did not give.
Ask everything in **one** AskUserQuestion call (max 4 questions), in plain words, no flag names:
1. **Which ministry?** Skipped if a name was given. Options: **only** names that are really in the registry (`input/ministries.json`, else
   `templates/ministries.default.json` next to the scripts), at most 3, each described as "Already in the registry". **Never invent or suggest ministries
   that are not in the registry to fill the list.** Always add one last option, "A different ministry", and if it (or the built-in "Other") is chosen,
   ask once in plain text: "Type the ministry's full official name." One ministry only.
2. **How thorough?** Skipped if `--profile` was given. Options: `Balanced (Recommended)` (normal speed and cost, verified twice where needed);
   `Fast` (cheaper and quicker, lighter checking); `Thorough` (slowest, re-checks every service).
3. **Is there an existing workbook to update?** Skipped if `--import` or `--resume` was given. Options: `No, start fresh (Recommended)`;
   `Yes, I have one` (then ask once, in plain text, for its file path).
4. **Which services to count?** Skipped if `--include-local` was given. Options: `National services only (Recommended)`; `National and Local (council) services`.
Map the answers to the flags below (`--profile`, `--import`, `--include-local`). Do not ask about `--set`, `--ministry-no` or `--resume`; those stay flag-only.
If AskUserQuestion is unavailable (a non-interactive session), do not ask: use the defaults above when a ministry name was given, otherwise stop and print the usage line.
After intake, say the choices in one line, then run autonomously as before. No more questions.

## Arguments
- `$ARGUMENTS[0]`: ministry name. If more than one ministry is given, **stop** and ask for one.
- `--import <xlsx>`: an existing workbook to use as the previous state (first run only; later runs use the ministry's current workbook).
- `--include-local`: also count Local (council) eServices services. The default is National only.
- `--profile fast|balanced|thorough`: speed/cost/quality preset (default: `default_profile` in `input/settings.json`, normally `balanced`).
  The resolved settings are printed at start and saved in `RUN/run.json`; `--resume` reuses them. See `docs/USER-GUIDE.md`.
- `--set key=value` (repeatable): override one setting, e.g. `--set verifier_model=opus --set max_parallel=6`.
  Keys: `roster_model`, `researcher_model`, `verifier_model`, `max_parallel`, `reverify_scope` (pending|all), `live_check` (script|llm),
  `verifier_chunk`, `audit_sample`, `audit_focus` (researcher_written|all).
- `--resume <RUN>`: continue an interrupted run (skip steps 1–2; `"$PY" -B "$S/run_state.py" pending RUN` lists what is left).
- `--ministry-no N`: override the registry number (normally taken from `input/ministries.json`, or the next free number).

## Workflow

```
init ─► roster ─► per agency: init file ─► research ─► check ─► verify ─► apply verdict ─► (repair ─► verify ─► final)
                                                                                         └─► build ─► validate ─► audit ─► report
```

1. **Init run**
   `"$PY" -B "$S/run_state.py" init --ministry "<name>" [--profile P] [--set k=v ...] [--import X] [--include-local] [--ministry-no N]`
   Keep `run` (= RUN), `ministry`, `ministry_no`, `existing_agencies` and `settings` (= SET) from its JSON output.
   Tell the user the profile and the models in one line. It also caches today's eServices catalogue.
   Use `SET.<key>` below. Pass `model: SET.<role>_model` on every Agent call for that role.
   If it prints `ERROR [ministry-no]`, stop and show the error (the registry file needs fixing).
   If the JSON has `warnings` (e.g. a Windows path that is too long), show them to the user once, then continue.

2. **Roster.** Start the `zambia-service-directory:zm-roster-builder` agent (`model: SET.roster_model`) with RUN, the ministry, MINISTRY_NO, and `existing_agencies`.
   Then for each agency: `"$PY" -B "$S/run_state.py" set RUN <slug> pending`, or `excluded` for Excluded ones.

3. **Research.** For each pending Included slug:
   `"$PY" -B "$S/agency_file.py" init RUN <slug>`, then start a `zambia-service-directory:zm-agency-researcher` agent (`model: SET.researcher_model`) with `RUN` and `SLUG`.
   Launch up to `SET.max_parallel` researchers in one message. Start the next agency as soon as one finishes.
   When one returns:
   - run `"$PY" -B "$S/agency_file.py" check RUN <slug>`;
   - if errors remain, send the researcher the findings once (`REPAIR` = the check findings);
   - then `run_state.py set RUN <slug> researched`.

4. **Verify** each researched agency as soon as it is ready (pipeline, don't wait for all). One verification round is:
   1. If `SET.live_check` is `script`: `"$PY" -B "$S/agency_file.py" live-check RUN <slug>` (fresh eServices comparison, about 20–70 s).
      Pass `LIVE=RUN/verify/<slug>.live.json` to the verifiers.
   2. `"$PY" -B "$S/agency_file.py" plan-verify RUN <slug> --scope <S> --chunk SET.verifier_chunk` with `<S>` = `all` for the first round and
      `SET.reverify_scope` for the round after a repair. It returns the passport indices per part.
   3. Start one `zambia-service-directory:zm-passport-verifier` (`model: SET.verifier_model`, fresh context, it must not see the researcher's log) per part, all in one message,
      each with `RUN`, `SLUG`, `INDICES`, `PART` (and `LIVE`). With a single part, omit `PART` and `INDICES` when the scope is `all`.
   4. With several parts: `"$PY" -B "$S/agency_file.py" merge-verdicts RUN <slug>`.
   5. `"$PY" -B "$S/agency_file.py" apply-verdict RUN <slug>`.
   If `repair > 0`: set the ledger to `repair`, move the round-1 verdict aside (`verify/<slug>.round1.json`), start a researcher with
   `REPAIR` = the `verification_notes` starting with `REPAIR:`, run one more verification round (scope `SET.reverify_scope`), then
   `apply-verdict RUN <slug> --final`. At most one repair round. Then set the ledger to `verified`, or `unresolved` if every passport failed.

5. **Ministry-wide check.** `"$PY" -B "$S/validate_passports.py" RUN/agencies/*.json --out RUN/validation.json`.
   Cross-agency errors (R-DUP-ID, R-REASSIGN) are fixed by one targeted researcher call for the agency named in the finding.

6. **Build.** `"$PY" -B "$S/build_workbook.py" --ministry "<ministry>" --agencies RUN/agencies/*.json --roster RUN/roster.json --ministry-no <no> [--import X] --report RUN/build.json`
   The previous workbook is archived to `versions/` automatically.
   Exit 1 means validation errors: fix them (step 5), never pass `--allow-errors` in a normal run.

7. **Validate the workbook.** `"$PY" -B "$S/validate_workbook.py" <workbook> --out RUN/workbook_validation.json --summary`.
   It must be `ok: true` for the rows this run wrote. Rows carried over from an older workbook may still fail
   (e.g. `Not specified`). They are listed in the report as not re-checked.

8. **Audit.** Run `/zambia-service-directory:zm-verify-ministry <workbook> --run RUN --sample SET.audit_sample --focus SET.audit_focus --verifier-model SET.verifier_model`.
   It writes `RUN/audit.json` and `RUN/audit.md`.

9. **Report.** `"$PY" -B "$S/report.py" RUN` → `RUN/report.md`. Then set all ledger entries to `done`.

## Final message to the user
- One line: `<Ministry>: <total> passports (+added / ~corrected / −removed), <n> agencies, audit PASS|FAIL`.
- A short per-agency table: agency | eServices count | passports | verifier result.
- Paths of the workbook, `report.md` and `audit.md`.
- Anything unresolved or carried over, and why.

## Rules
- One ministry per run. Never mix ministries in one workbook.
- eServices data only via `eservices.py` (public JSON API). Never scrape the directory page.
- Never write a value without an official source (tiers 1–4). Tier 5 (LinkedIn, news) is for leads only.
- Never hand-edit the workbook. Always go through `build_workbook.py`, so every ministry file keeps the identical format from `templates/workbook_spec.json`.
- Missing information is written as `Not published`. Never invent a value.
- Ministry numbers live in `input/ministries.json`. Unknown ministries get the next free number.
- If subagents are unavailable, play the roles yourself as described in `$R/ROLES.md`.
- Logs: set `LOG_LEVEL=DEBUG` for verbose script output. `ZM_RUN_DIR=RUN` also writes `RUN/run.log`.
