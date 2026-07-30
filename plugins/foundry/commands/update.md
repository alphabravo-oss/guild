---
description: "Update the foundry MCP server to the latest version"
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/update-mcp.sh:*)"]
hide-from-slash-command-tool: "true"
---

# Foundry Update Command

Upgrade the foundry MCP server to the latest published code. Does not reinstall
prerequisites, does not touch `.mcp.json`, does not restart anything else — use
`/foundry:setup` for a first-time install.

Run the update:

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/update-mcp.sh" $ARGUMENTS
```

After the script completes:

1. If the version changed, tell the user to **restart Claude Code** to load the
   new server.
2. If it reports "Already up to date", say so and stop — no restart needed.
3. If it failed, surface the traceback verbatim. A rebuild that cannot start is
   almost always a dependency that resolved to an incompatible major version,
   not a problem with the user's setup.

**Why this command exists:** the MCP entry points uvx at an unpinned git URL,
and uvx caches the git build by resolved commit — it does not re-resolve on
later runs. Without an explicit refresh the same commit is served indefinitely.
This command is that refresh.

Forge plans. Foundry builds.
