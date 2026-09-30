# Zambia Claude Plugins

Public plugin marketplace for the Zambia National Service Directory harness.

Repo: `https://github.com/CaisinIon/zambia-claude-plugins`

## Install (no account or login needed)

This repo is public, so `claude plugin marketplace add` can clone it with no GitHub account,
token or login on your side:

```bash
claude plugin marketplace add CaisinIon/zambia-claude-plugins
claude plugin install zambia-service-directory@zambia
```

Restart Claude Code, then confirm:

```bash
claude plugin list
```

If `marketplace add` fails, run `git clone https://github.com/CaisinIon/zambia-claude-plugins` once by hand to confirm the
repo is reachable (network/DNS, or the repo not published yet), then retry.

## Automatic provisioning

Any project repo can declare this marketplace so teammates get the plugin with no manual setup:

```bash
cd /path/to/your/project
claude plugin marketplace add CaisinIon/zambia-claude-plugins --scope project
claude plugin install zambia-service-directory@zambia --scope project
```

That writes `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "zambia": {
      "source": {"source": "github", "repo": "CaisinIon/zambia-claude-plugins"}
    }
  },
  "enabledPlugins": ["zambia-service-directory@zambia"]
}
```

Commit the file — a fresh clone is then ready to go.

## Plugins

| Plugin | What it does |
|---|---|
| `zambia-service-directory` | Researches one Zambian ministry's agencies and public services, verifies every service passport, and writes the ministry's National Service Directory Excel workbook. See [plugins/zambia-service-directory/README.md](plugins/zambia-service-directory/README.md). |

## Updating the plugin

This repo's `plugins/zambia-service-directory/` is generated, not hand-edited — it is built from the
harness's own `.claude/skills/` and `.claude/agents/` in the `zambia-service-directory` project.
Regenerate it there:

```bash
python3 .claude/skills/zm-ministry-directory/scripts/package.py --target plugin \
  --out /path/to/this/repo --gh-user CaisinIon
```

Bump `version` in `plugins/zambia-service-directory/.claude-plugin/plugin.json` (or pass `--version`) before
tagging a release:

```bash
claude plugin tag plugins/zambia-service-directory
git push --tags
```

Teammates update with `claude plugin update zambia-service-directory` (restart required).

## Validate before pushing

```bash
claude plugin validate .
claude plugin validate plugins/zambia-service-directory
```

## Secrets

Never commit tokens or credentials. This plugin has none by default; the optional Chrome
DevTools MCP server runs via `npx` and needs none either.
