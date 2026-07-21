---
description: Run the Playwright suite, read trace.zip per failure, triage by root cause
---

The user wants you to run the test suite and triage failures. Optional `--fix` flag in $ARGUMENTS means propose patches (do not auto-apply).

## Preflight

1. `playwright.config.ts` exists.
2. Read `plugins/e2e/skills/diagnose/SKILL.md` (or invoke the diagnose skill) — it has the trace-reading methodology and failure taxonomy.

## Run the suite

```bash
npx playwright test --reporter=json
```

Capture stdout to a temp file (the JSON is large). Parse it. Don't pipe directly into Read — it'll be unwieldy. Write to `/tmp/e2e-audit-$$.json` then read.

If all tests pass: report counts and exit. No triage needed.

## Triage each failure

For each failed test, gather:

1. **Failure message** — from the JSON `errors[]` array
2. **Stack** — first frame in user code (skip Playwright internals)
3. **Trace** — at `test-results/<test-folder>/trace.zip`. Get its summary via:
   ```bash
   unzip -p test-results/<folder>/trace.zip trace.trace | head -200
   ```
   Or, if the trace structure makes that unwieldy, invoke MCP and open the HTML report:
   ```bash
   npx playwright show-report
   ```
   and use `browser_navigate` to the failed test's page in the report.
4. **Screenshot** — `test-results/<folder>/test-failed-1.png` if present

## Classify by root cause

Use this taxonomy (mirrored from the diagnose skill — keep them in sync):

| Class | Signature | Fix direction |
|---|---|---|
| **SELECTOR_DRIFT** | "locator resolved to N elements" or "not found" + DOM snapshot in trace shows the element with different text/role | Re-snapshot, update locator. Prefer `getByRole`/`getByLabel` over text matches when DOM is volatile. |
| **TIMING** | "Timeout exceeded" but element does eventually appear in later snapshot | Replace brittle wait with `expect(locator).toBeVisible({ timeout })`. Increase timeout only after confirming the load is legitimately slow. |
| **ASSERTION_MISMATCH** | Assertion failed but page rendered normally | Either the test's expectation is wrong (product changed intentionally) or the product regressed. Surface both possibilities — don't auto-decide. |
| **CONSOLE_ERROR** | Test caught a `console.error` that wasn't there before | Real product bug. Capture the error text and stack frame from the page. Don't suppress in the test. |
| **NETWORK_FAIL** | Failed request in trace network panel | Check if the failing endpoint is the test's target or incidental. Mock incidental ones, fix or flag the target. |
| **STATE_POLLUTION** | Passes alone, fails in suite. Trace shows unexpected initial state. | A prior test left state. Add fixture cleanup or isolate via `test.use({ storageState })`. |
| **FLAKY** | Passes on retry. Trace shows non-deterministic ordering. | Identify the race (animation, debounce, async render). Wait for the stable state explicitly, don't sleep. |

For each failure, output:

```
<spec_path>:<line>  [<CLASS>]  <one-line summary>
  Root cause: <what the trace shows>
  Fix direction: <what to change, in 1-2 sentences>
```

Group identical root causes — if 8 tests fail the same way after a button got renamed, that's one finding, not eight.

## --fix mode

If `--fix` is in $ARGUMENTS:

1. Generate the proposed patch for each finding (as a diff or Edit tool plan)
2. Present them all at once
3. **STOP** — wait for the user to ack before applying. Per repo convention: do not auto-apply patches. Edits to spec files are reversible but cluttering the diff with mass changes annoys reviewers.

If the user acks, apply via Edit, then re-run the suite, then report final pass/fail.

## Report shape

```
Audit: <N> tests run, <P> passed, <F> failed in <T>s

Failures by class:
  SELECTOR_DRIFT: <count>
  TIMING: <count>
  ...

Findings:
  [SELECTOR_DRIFT] tests/login.spec.ts:14 — "Sign in" button renamed to "Log in"
    Fix: update locator to getByRole('button', { name: /sign|log in/i })

  ...

Suggested next: <action based on findings>
```

Never report `<F> failed` and not show every failure. The triage IS the value — don't hide it behind summaries.
