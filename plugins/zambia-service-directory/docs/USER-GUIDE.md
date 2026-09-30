# User Guide: Zambia Ministry Service Directory

This tool researches **one Zambian ministry at a time**. It checks every agency's public services against Zambia eServices and other official sources. Then it writes the ministry's Excel workbook, `Zambia_National_Service_Directory_<Ministry_Name>.xlsx`, in the same fixed format every time.

It runs inside Claude Code. In the Claude Desktop app, use the **Code tab**.

## 1. Before you start

You need:
- The `zambia-service-directory` folder (built with `package.py`, or this project itself).
- Python 3 on the computer, **or** the free `uv` tool, which downloads its own Python (recommended for customers: `curl -LsSf https://astral.sh/uv/install.sh | sh`). The first run sets everything else up by itself.
- Internet access to `eservices.gov.zm` and to the government and legal websites.
- Optional: the Chrome DevTools tool (`.mcp.json`) for websites that only show content with JavaScript.

## 2. Install

### 2a. As a plugin (recommended)

If someone has published this harness as a Claude Code plugin (see docs/harness.md → Packaging),
install it once per machine:

```bash
claude plugin marketplace add <owner>/zambia-claude-plugins
claude plugin install zambia-service-directory@zambia
```

Restart Claude Code, then open (or create) any folder you want a ministry's output written into,
and run the commands from there — that folder is where `input/`, `output/` and `cache/` end up,
never the plugin's own install location. The first command you run bootstraps a private Python
environment automatically; there is no separate setup step.

Commands are namespaced under the plugin: `/zambia-service-directory:zm-ministry-directory` and
`/zambia-service-directory:zm-verify-ministry`. The rest of this guide writes them unprefixed
(`/zm-ministry-directory`); if you installed the plugin, add that prefix.

### 2b. As a folder, in Claude Desktop (alternative)

1. Copy the `zambia-service-directory` folder to where you keep projects.
2. Open Claude Desktop, go to the **Code** tab, and choose that folder as the working folder.
3. Ask Claude: `run export ZM_ROOT="$(pwd)"; if command -v uv >/dev/null 2>&1; then uv run --no-project --python 3.12 ${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/scripts/bootstrap.py --venv ${CLAUDE_PLUGIN_DATA}/venv; else python3 ${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/scripts/bootstrap.py --venv ${CLAUDE_PLUGIN_DATA}/venv; fi`. This creates `.venv` and installs what the tool needs. It takes about a minute and is needed once.
4. First check, which does not cost much: `/zambia-service-directory:zm-verify-ministry ${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/templates/reference_workbook.xlsx`. It should finish with a verdict, which tells you the skills are loaded.

> Not yet verified inside the Desktop app: that the three helper agents start and that the Chrome tool works. If an agent does not start, the tool plays the roles itself, one after another (slower, same rules).

## 3. Run a ministry

```
/zm-ministry-directory "Ministry of Tourism"
```

(Plugin install: `/zambia-service-directory:zm-ministry-directory "Ministry of Tourism"`.)

You can also type just `/zm-ministry-directory` with nothing after it. Claude then asks a few plain questions in pop-ups: which ministry, how thorough, whether you have a workbook to update, and whether to count Local (council) services. You never need to know the option names.

After those questions the run is autonomous. It does not stop again. At the end you get a short summary and the file paths.

Common options:

| Option | What it does |
|---|---|
| `--profile fast\|balanced\|thorough` | Speed, cost and depth of checking (section 4). The default is `balanced`. |
| `--import old.xlsx` | Start from an existing workbook. Every row in it is re-checked. |
| `--resume output/<Ministry>/runs/<time>` | Continue a run that was interrupted. It keeps the same profile. |
| `--include-local` | Also count Local (council) services from eServices. The default is National only. |
| `--ministry-no 7` | Force the ministry number (normally taken from `input/ministries.json`). |
| `--set key=value` | Change one setting for this run only (section 5). |

Run **one ministry per command**. To do several, run the command once for each.

### What happens during a run

1. Finds the ministry's agencies (`roster`).
2. Researches each agency: eServices first, then the agency's own site, forms, fee schedules and laws.
3. An independent reviewer checks every service against its sources. Failures get one repair round.
4. Builds the workbook. The previous copy is saved in `versions/`.
5. Checks the workbook format and totals, then audits a random sample of rows.
6. Writes `report.md`.

## 4. Profiles: speed, cost and depth

Pick a profile when you start. The figures for tokens and clock time are **estimates** from one real run (Ministry of Tourism, 8 agencies: about 2.0M tokens and 34 minutes, with Sonnet researchers, Opus verifiers and everything re-verified). The profiles were not run end to end, so treat the numbers as rough.

| Profile | Who checks | How much is re-checked | Audit sample | Est. tokens | Est. clock |
|---|---|---|---|---|---|
| `fast` | Sonnet 5.5 | only changed passports; eServices values by script | 6 rows | 1.1–1.3M | 20–25 min |
| `balanced` (default) | Opus 5.5 | only changed passports; eServices values by script | 12 rows | 1.5–1.6M | 25–28 min |
| `thorough` | Opus 5.5 (also researches) | everything again after a repair; agents re-fetch eServices | 25 rows | 2.0M+ | 35+ min |

Choose:
- `balanced` for normal work.
- `fast` for a first pass, a quick update of a ministry you already know, or when the budget is tight. **In a small test, a Sonnet verifier missed 2 of 3 planted errors** (a missing Act in the legal references, and applicants that no source supports). Opus had flagged both in the real run, though the applicants error only in the final audit. Use `fast` only if someone reviews the report afterwards.
- `thorough` for a ministry that will be published, or one with heavy legal content.

The three savings that do not affect quality (`balanced` and `fast` use them):
- **Only changed passports are re-checked after a repair.** Passports that already passed are left alone.
- **A script compares eServices values with the live API.** Agents no longer re-fetch each service by hand.
- **Big agencies are checked in parallel parts.** This shortens the clock time (the slowest agency dominated the last run), but costs more tokens, because each part re-reads the shared sources (about 35% more in one test on a 7-passport agency: 137k vs 102k tokens, 225 s vs 354 s). Only agencies larger than `verifier_chunk` are split; set `verifier_chunk=0` to never split.

## 5. Settings you can change

Profiles live in `input/settings.json`. Edit that file to change them for every run, or use `--set` for one run:

```
/zm-ministry-directory "Ministry of Lands" --profile fast --set max_parallel=8 --set audit_sample=10
```

| Setting | Values | Meaning |
|---|---|---|
| `roster_model`, `researcher_model`, `verifier_model` | `claude-sonnet-5-5`, `claude-opus-5-5`, or `sonnet` / `opus` | Model for each role. The verifier has the most effect on cost and on catching errors. |
| `max_parallel` | 1 or more | How many agencies are researched at the same time. More is faster; the longest agency still sets the minimum clock time. |
| `reverify_scope` | `pending` or `all` | After a repair, re-check only the changed passports, or all of them. |
| `live_check` | `script` or `llm` | `script`: a program compares eServices values with the live API. `llm`: the reviewer does it by hand (slower, costs more). |
| `verifier_chunk` | 0 or more | Agencies with more passports than this are checked in parallel parts: faster, but more tokens. `0` = never split. |
| `audit_sample` | 0 or more | How many rows the final audit re-checks. |
| `audit_focus` | `researcher_written` or `all` | `researcher_written` samples rows that were not copied straight from eServices, since these carry the judgement calls. |

To see what a profile will do before running it: `python3 ${CLAUDE_PLUGIN_ROOT}/skills/zm-ministry-directory/scripts/settings.py show --profile fast`.

The three helper agents also have a default model in their own files (`${CLAUDE_PLUGIN_ROOT}/agents/zm-*.md`). The profile's model wins during a run.

## 6. Ministry numbers

`input/ministries.json` lists each ministry's number:

```json
[
  {"ministry_no": 3, "name": "Ministry of Tourism", "acronym": "MoT", "website": "https://www.mot.gov.zm"}
]
```

- Replace it with your own list at any time. Only `ministry_no` and `name` are required.
- A ministry that is not in the file gets the next free number (highest + 1), and the file is updated so it never changes.
- Two ministries with the same number or name stop the run with an error.

## 7. Where the results are

```
output/<Ministry_Name>/
  Zambia_National_Service_Directory_<Ministry_Name>.xlsx   the latest workbook
  versions/…_v001_<date>.xlsx                              earlier copies (made automatically on every rebuild)
  runs/<time>/
    report.md      start here: per-agency results and totals
    audit.md       the independent audit and its verdict
    roster.json, agencies/, verify/, evidence/, logs/   the working files
```

`evidence/` keeps a saved copy of every web page or PDF a value came from, so anyone can check it later.

## 8. Reading the results

**The workbook** has three sheets:
- **Overview**: ministry, number of agencies and passports, and a count per agency.
- **Agencies**: one row per agency, with a `Status` (`Included` or `Excluded`) and a `Notes` cell in a fixed wording, e.g. "7 verified services: 5 on Zambia eServices and 2 on the MoT Casino Licensing downloads page".
- **Service Passport**: one section per agency with the 11 fixed columns. Missing information is always `Not published`.

**The report** (`report.md`), for each agency, shows: official name, eServices status, exact eServices count, services verified, corrected passports, additional services from other official sources, source limits and conflicts, historical-fee warnings, and the running ministry total. At the top are totals: added, corrected, removed, kept without re-check, unresolved.

**The audit verdict** (`audit.md`) is PASS or FAIL. A FAIL is not always a problem with the data. It also appears when:
- kept rows still say `Not specified` (the old wording), or
- the audit sample found errors after the workbook was built.
Read the table in `audit.md`. It says what to fix and where.

### Words you will see

| Word | Meaning |
|---|---|
| Verified / Verified with limitations | Checked against sources. "With limitations" says what could not be confirmed (for example a blocked website). |
| Unresolved | No official source could be found. The row is **not rewritten**. The old row stays and is flagged. |
| Carried over / kept without re-check | An old row that this run neither confirmed nor removed. |
| Historical fee | A fee from an old source. The exact label is "Historical fee — current amount requires confirmation". |
| Excluded | The agency has no public-facing service (for example only an information page). |
| Reassigned | An eServices service listed under one agency but delivered by another. It is counted once, under the agency that delivers it. |

## 9. What to do after a run

1. Open `report.md`, read the totals and the "Unresolved" and "Carried over" lists.
2. Open the workbook in Excel and spot-check 5–10 changed rows against the official source shown in `Source / Service Link`.
3. For unresolved rows: ask the agency for the source, or remove the row (delete it from the workbook, or say so in the next run).
4. Run again later with the same command to update. The old workbook is kept in `versions/`.
5. Share `report.md` or the summary with the team.

## 10. Updating a ministry later

Run the same command again. The tool uses the ministry's current workbook as the starting point. Every row is re-checked, and the report shows what was added, corrected or removed since the last version.

## 11. Costs and time

- One run costs tokens (a real Ministry of Tourism run: about 2.0M subagent tokens, plus the main session's own, which we could not measure).
- Cost grows with the number of passports, not agencies. A large agency (28 passports) cost about 5 times a tiny one.
- Checking (the verifier) is the biggest part, about 60% of the tokens.
- For money amounts, check your account's usage dashboard. The tool cannot see prices.

## 12. If something goes wrong

| Problem | What to do |
|---|---|
| `ERROR [ministry-no]` | `input/ministries.json` has duplicate numbers or names. Fix the file. |
| `ERROR [template-conflict]` | The format template was changed. Restore `templates/reference_workbook.xlsx`, or read section 13. |
| The run stopped or timed out | Run it again with `--resume output/<Ministry>/runs/<time>`. Finished agencies are not repeated. |
| A website is blocked (403 or a login page) | Not an error. The report lists it under source limits. Sources that can't be opened are never guessed. |
| Agents do not start in Desktop | The tool then plays each role itself (see `references/ROLES.md`). It is slower but follows the same rules. |
| Too slow or too costly | Use `--profile fast`, or lower `audit_sample` and `verifier_chunk`. |
| The audit says FAIL | Read `audit.md`. See section 8. |
| Something looks wrong in the workbook | Do not edit it by hand. Report it; the fix belongs in the tool's rules so every ministry benefits. |

Verbose logging: set `LOG_LEVEL=DEBUG`. Add `ZM_RUN_DIR=<run folder>` to also save the log as `run.log`.


## 14. What the tool does not do

- It does not decide policy: whether a public-register search counts as a service is a rule in `references/TERMINOLOGY.md` (today: no). Change it there and say so.
- It cannot read sites that block automated access. It uses other official copies and archives instead, and says so.
- Old fees stay in their original unit and are labelled historical, unless an official source for the current value is saved.
- It cannot prove a value is right. It checks values against sources and records conflicts. A person should still review anything that will be published.
