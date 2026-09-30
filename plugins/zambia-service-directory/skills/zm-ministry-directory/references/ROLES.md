# Roles (fallback when subagents are not available)

The harness normally runs three subagents from `${CLAUDE_PLUGIN_ROOT}/agents/`. If the environment cannot start subagents
(for example Cowork, or a single-agent session), the orchestrator plays each role itself, one after another:

| Role | Instructions | Reads | Writes |
|---|---|---|---|
| Roster builder | `${CLAUDE_PLUGIN_ROOT}/agents/zm-roster-builder.md` | ministry name, previous workbook agencies | `RUN/roster.json`, `RUN/logs/roster.md` |
| Agency researcher | `${CLAUDE_PLUGIN_ROOT}/agents/zm-agency-researcher.md` | one `SLUG` | `RUN/agencies/SLUG.json`, `RUN/evidence/SLUG/`, `RUN/logs/SLUG.md` |
| Passport verifier | `${CLAUDE_PLUGIN_ROOT}/agents/zm-passport-verifier.md` | `RUN/agencies/SLUG.json` + evidence **only** | `RUN/verify/SLUG.json` |

Rules for playing the roles yourself:
1. Read the role file and follow it exactly, including its hard rules and output format.
2. Keep the verifier independent. Before verifying an agency, do not re-read `RUN/logs/SLUG.md`,
   and judge only what the agency file and saved evidence show. If context allows, verify each agency
   in a fresh session (`--resume RUN` continues where the ledger stopped).
3. Work through agencies one at a time, in roster order, and update the ledger after each step (`run_state.py set`).
