---
description: Drive the browser via Playwright MCP to perform a user flow, emit a *.spec.ts, run it green
---

The user wants you to write a Playwright E2E test for a specific user flow. Flow description is in $ARGUMENTS.

## Preflight

1. Confirm `playwright.config.ts` exists in cwd. If not, tell them to run `/e2e:init` first and STOP.
2. Read `${CLAUDE_PLUGIN_ROOT}/skills/author/SKILL.md` (or invoke the author skill) — it has the selector/wait/idempotency rules you must follow.
3. Confirm Playwright MCP tools are loaded (browser_navigate, browser_snapshot, etc.). If they aren't loaded, tell the user to restart Claude Code and STOP.
4. Confirm their dev server is reachable at the baseURL in `playwright.config.ts`. Try `curl -sI $BASE_URL` (default `http://localhost:3000`). If unreachable, ask the user to start it.

## For non-trivial flows: delegate to the spec-writer subagent

If the flow description is more than one screen of interaction (multi-step form, login + action, cross-page workflow), spawn the `e2e:spec-writer` agent. It runs in isolated context which keeps the MCP snapshot noise out of the main thread. Pass it the flow description, baseURL, and any constraints.

For simple single-action flows (one click, one assertion), do it inline — spawning an agent is overhead.

## Procedure (inline path)

### 1. Slug the spec

Convert the flow description to a kebab-case slug. Example: "user logs in and sees dashboard" → `login-shows-dashboard`. Target path: `tests/<slug>.spec.ts`. If a file already exists at that path, append `-2`, `-3`, etc. — never silently overwrite.

### 2. Drive the browser

Use Playwright MCP:

1. `browser_navigate` to baseURL (and the starting route, if the flow names one).
2. `browser_snapshot` — read the accessibility tree. Identify the elements you'll interact with by their roles and accessible names.
3. For each step in the user flow: pick the MCP tool that matches (`browser_click`, `browser_type`, `browser_select`, `browser_press_key`, etc.) and execute.
4. After each step, take another `browser_snapshot` to confirm the DOM changed as expected and to capture the post-state for assertions.
5. Note any console errors during the flow — they belong as assertions in the spec.

### 3. Emit the spec

Write `tests/<slug>.spec.ts`. Follow these rules (also in the author skill):

- Use `import { test, expect } from '@playwright/test'`
- One `test.describe` per spec file, one or more `test()` blocks inside
- Use `page.getByRole(...)`, `page.getByLabel(...)`, `page.getByText(...)` for locators — **NOT** CSS selectors (`.btn-primary`) or XPath, except as last resort with a comment explaining why
- Wrap multi-step interactions in `test.step('human-readable label', async () => { ... })` so trace.zip is readable
- Never use `page.waitForTimeout(ms)` — rely on Playwright's auto-wait via `await expect(locator).toBeVisible()` or `.toHaveText()`
- Each `test()` must be self-contained — set up its own state, do not depend on prior test runs
- Assertions go on what the user sees, not implementation details (URL is fine; localStorage keys are not, unless that's the actual product behavior being tested)

### 4. Run it green

```bash
npx playwright test tests/<slug>.spec.ts
```

If it passes, report the path and the test count.

If it fails:
1. Read the failure output. If it's a selector miss, re-snapshot the page, find the real accessible name, fix the locator.
2. If it's a timing issue, replace the brittle wait with an `expect(...).toBeVisible({ timeout: ... })` against a stable post-condition.
3. Retry up to 3 times. After 3 failures, STOP and report what's blocking — don't enter an infinite loop. The trace at `test-results/<test>/trace.zip` is the source of truth; tell the user how to view it (`npx playwright show-trace test-results/<test>/trace.zip`).

### 5. Report

Tell the user:
- Path to the new spec
- Pass/fail and run time
- Any console errors you observed during the flow that aren't covered by assertions (suggest adding them)
- Suggest `/e2e:audit` to run the full suite and check nothing else broke

## What you do NOT do

- Do not invent flows the user didn't describe. If the description is ambiguous, ASK before driving the browser.
- Do not add tests for hypothetical edge cases the user didn't request — the user asked for *this* flow.
- Do not commit. Writing the spec is your scope; committing is theirs.
