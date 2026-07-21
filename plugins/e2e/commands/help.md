---
description: Show the e2e plugin commands, skills, and agent
---

The user wants help with the e2e plugin. Reply with exactly this message — verbatim, no additions:

```
e2e — Playwright E2E test authoring + maintenance, driven by Claude

WHAT IT DOES
  Wraps Playwright + Playwright MCP into a slash-command workflow. Claude drives
  a real browser, reads the accessibility tree, writes *.spec.ts files that
  pass, and keeps them passing. Author specs from prompts, generate smoke
  coverage across every route, lock down access control via routes × roles,
  triage failures from trace.zip.

COMMANDS
  /e2e:init                  Install Playwright + register MCP + scaffold tests/
  /e2e:write <flow>          Claude drives the browser, emits *.spec.ts, runs green
  /e2e:crawl [baseURL]       Discover every route → one smoke spec per route
  /e2e:matrix                Routes × roles coverage with reused storageState
  /e2e:audit [--fix]         Run suite, read trace.zip, triage by root cause
  /e2e:record <slug>         Wrap `playwright codegen` for manual recording
  /e2e:help                  This message

SKILLS
  e2e:author     Selector hygiene, auto-wait discipline, idempotent test design
  e2e:diagnose   Trace.zip reading, flaky-test triage, failure taxonomy

SUBAGENT
  e2e:spec-writer    Isolated MCP-driven spec authoring. Spawned by /e2e:write
                     for multi-step flows. Invoked via Agent tool with
                     subagent_type=e2e:spec-writer.

CONFIG
  playwright.config.ts   Scaffolded by /e2e:init. baseURL via $BASE_URL env.
  .mcp.json              Playwright MCP entry (headed by default).
  e2e.config.json        Roles + login flow for /e2e:matrix. Created on first
                         matrix run.
  .auth/                 storageState per role. Gitignored — contains session
                         tokens. Never commit.

TYPICAL FLOW
  1. /e2e:init                              (once per project)
  2. /e2e:crawl http://localhost:3000       (smoke baseline)
  3. /e2e:write "user logs in and adds a todo"   (per-feature specs)
  4. /e2e:audit                             (run + triage on each PR)
  5. /e2e:matrix                            (when access control matters)

PRINCIPLES
  - getByRole over CSS. Auto-wait over sleep. Idempotent over chained.
  - Observe first, lock assertions second. Especially in /e2e:matrix.
  - Trace.zip is the source of truth for failures. Don't guess from messages.
  - Don't commit .auth/.
```

Do not embellish, summarize, or rephrase. The verbatim block IS the help.
