# zambia-service-directory

Claude Code plugin: researches one Zambian ministry's agencies and public services (Zambia
eServices + official sources), verifies every service passport, and writes the ministry's
National Service Directory Excel workbook.

## Install

```bash
claude plugin marketplace add <gh-user>/zambia-claude-plugins
claude plugin install zambia-service-directory@zambia
```

(Ask whoever maintains the marketplace for the real `<gh-user>/zambia-claude-plugins` URL.)

## Use

| Command | What it does |
|---|---|
| `/zambia-service-directory:zm-ministry-directory "Ministry of Tourism"` | Full autonomous run: roster → research → verify → workbook → audit → report |
| `/zambia-service-directory:zm-ministry-directory "Ministry of Tourism" --profile fast` | `fast`, `balanced` (default) or `thorough`: speed, cost and depth of checking |
| `/zambia-service-directory:zm-ministry-directory "Ministry of Tourism" --import old.xlsx` | Same, using an existing workbook as the starting point |
| `/zambia-service-directory:zm-ministry-directory "…" --resume output/<Ministry>/runs/<ts>` | Continue an interrupted run |
| `/zambia-service-directory:zm-verify-ministry output/<Ministry>/Zambia_National_Service_Directory_<Ministry>.xlsx` | Independent audit of any workbook (PASS/FAIL) |

Run these from the project folder you want the output written into — that folder is where
`input/`, `output/` and `cache/` live, never this plugin's own install folder.

## First run

The first command bootstraps automatically: it creates a Python virtual environment under
Claude Code's per-plugin data folder (`${CLAUDE_PLUGIN_DATA}/venv`, resolving to something like
`~/.claude/plugins/data/zambia-service-directory/venv`) and installs `requirements.txt` from this plugin.
That environment is separate from any `.venv` in your project; it survives plugin updates but
not an uninstall.

Optional: the bundled Chrome DevTools MCP server (`.mcp.json` in this plugin), for
JavaScript-only ministry websites. It starts automatically via `npx`; no setup needed.

## Ministry numbers

`input/ministries.json` in your project holds the fixed `ministry_no` per ministry. A fresh
project seeds it from this plugin's own starting registry (currently just
Ministry of Tourism = 3); unknown ministries get the next free number, saved back into your
project's `input/ministries.json`.

## Settings

An optional `input/settings.json` in your project overrides the built-in speed/cost/depth
profiles (`fast`, `balanced`, `thorough`). See `--profile` and `--set key=value` above, and
[docs/USER-GUIDE.md](docs/USER-GUIDE.md).

## Output

```
output/<Ministry_Name>/
  Zambia_National_Service_Directory_<Ministry_Name>.xlsx   latest version
  versions/…_v001_<date>.xlsx                              previous versions
  runs/<timestamp>/  report.md · audit.md · roster.json · agencies/ · verify/ · evidence/ · logs/
```

## Docs

- [docs/USER-GUIDE.md](docs/USER-GUIDE.md): running, choosing a profile, reading results, fixing problems
- [docs/harness.md](docs/harness.md): architecture, sources, rules, format, resume, limitations
- `skills/zm-ministry-directory/references/TERMINOLOGY.md`: fixed wording and counting rules
