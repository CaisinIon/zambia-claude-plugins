#!/usr/bin/env node
// Starts the Chrome DevTools MCP server through npx on macOS, Linux and Windows.
// On Windows `npx` is `npx.cmd`, which Claude Code cannot start directly (the server then never connects);
// a shell can. `node` is a real program on every system, so .mcp.json starts this file instead of `npx`.
// Extra arguments are passed on to chrome-devtools-mcp.
const { spawn } = require("node:child_process");

const args = ["-y", "chrome-devtools-mcp@latest", "--no-usage-statistics", ...process.argv.slice(2)];
const child = spawn("npx", args, { stdio: "inherit", shell: process.platform === "win32" });
child.on("error", (err) => {
  console.error(`[chrome-mcp] cannot start npx (is Node.js installed?): ${err.message}`);
  process.exit(1);
});
child.on("exit", (code, signal) => process.exit(code === null ? (signal ? 1 : 0) : code));
