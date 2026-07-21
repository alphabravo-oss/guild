---
name: diagnose
description: Trace-driven Playwright failure triage. How to read trace.zip, classify failures by root cause, and tell flaky tests from real regressions. Used by /e2e:audit and any time you're staring at a red spec.
user_invocable: false
---

# e2e:diagnose — Playwright Failure Triage

A Playwright failure message is a hypothesis. The trace is the evidence. Always read the trace before proposing a fix.

## 1. Get to the trace

Three paths to the trace, in order of preference:

1. **HTML report** — `npx playwright show-report`. Click the failed test. Click "Trace" tab. Best UX, requires GUI.
2. **Trace viewer directly** — `npx playwright show-trace test-results/<test-folder>/trace.zip`. Same UI, no report navigation.
3. **Raw trace dump** (headless / SSH) — `unzip -p test-results/<folder>/trace.zip trace.trace` — JSONL events. Grep for `console`, `error`, `actionEnd`. Painful but works.

Every failed test has its own `test-results/<test-folder>/` with `trace.zip`, screenshots, and video (if `video: retain-on-failure` is set).

## 2. Classify by root cause

This taxonomy matches `/e2e:audit`. Use the same class names.

### SELECTOR_DRIFT

**Signal:** `locator.click: Target page, context or browser has been closed` or `Error: strict mode violation: getByRole('button') resolved to N elements` or simply `not found`.

**Confirm via trace:** In the snapshot at the moment of failure, does the element exist with a *different* accessible name? E.g., the test looked for `'Sign in'`, but the button is now `'Log in'`.

**Fix:** Re-snapshot via MCP. Update the locator to match current reality. Prefer adding flexibility (regex name, or anchoring on a stable sibling) over hardcoding the new value — the next rename is coming.

### TIMING

**Signal:** `Timeout 5000ms exceeded waiting for ...`. Element does eventually appear if you scroll the trace timeline forward past the failure point.

**Confirm via trace:** Find the timestamp of the failed wait. Look at later snapshots. Does the element show up? If yes, this is timing. If no, it's something else (probably SELECTOR_DRIFT or a real regression).

**Fix:**
- First instinct: replace ad-hoc waits with `await expect(locator).toBeVisible({ timeout: 10_000 })` — auto-wait polls properly.
- If the page is genuinely slow (initial JS load, big data fetch), increase the timeout. Don't blanket-bump every wait — bump only the specific one.
- If a network call is the bottleneck, `await page.waitForResponse(url)` against the specific endpoint is more precise than a visual wait.

### ASSERTION_MISMATCH

**Signal:** Assertion failed, but the trace shows the page rendered fine. E.g., `expected 'Welcome, Alice' but got 'Welcome, alice'`.

**Confirm via trace:** Did the prior steps succeed? Is the page in the expected state? If yes, this is a real difference between expectation and reality.

**Fix path is a JUDGMENT call:**
- The product changed intentionally → update the assertion
- The product regressed → file a bug, don't update the assertion
- Surface BOTH options to the user. Do not silently relax assertions; that's how regressions get masked.

### CONSOLE_ERROR

**Signal:** Test caught a `console.error` event via the listener (see author skill rule #4).

**Confirm via trace:** Open the Console tab in the trace viewer. Read the actual error message and stack. The error came from the app, not from Playwright.

**Fix:** The error is a real product bug, almost always. Don't suppress in the test — fix the product, OR if the error is from a third-party script and genuinely benign, filter it specifically in the listener (`if (text.includes('Known-OK')) return`).

### NETWORK_FAIL

**Signal:** Trace network panel shows a failed request (red, 4xx/5xx, or canceled).

**Confirm via trace:** Was the failing request the one the test depends on? Or incidental (e.g., a third-party analytics endpoint)?

**Fix:**
- Test's target endpoint failing → real bug or environment issue. Check if the dev server has the required state (DB seeded, env vars set).
- Incidental endpoint failing → mock it in the test via `page.route(...)` so the test is hermetic.

### STATE_POLLUTION

**Signal:** Test passes alone (`npx playwright test specific.spec.ts`), fails in suite (`npx playwright test`). Trace shows unexpected initial state — leftover data, residual auth, modified preferences.

**Confirm via trace:** Look at the first snapshot. Is the page in a "fresh" state, or does it look like a previous test left things behind?

**Fix:**
- Use `test.use({ storageState })` to ensure each test starts authenticated as a known user, not whoever happened to log in last
- Add a `beforeEach` cleanup or use a fixture that creates unique data per test
- If the database is shared and dirty, consider seeding fresh data per test (slower but reliable)

### FLAKY

**Signal:** Passes on retry. Trace from the failing attempt and passing attempt differ — different DOM ordering, different timing of async ops.

**Confirm:** Run with `--repeat-each 10`. If 8/10 pass, it's flaky. Real failures are deterministic.

**Fix paths — pick one, don't paper over:**
- **Race condition:** Identify the two events whose order is not guaranteed. Wait for the stable post-state explicitly. Example: form auto-saves on blur; test asserts immediately. Wait for the "Saved" toast.
- **Animation:** Wait for animation end via `await locator.evaluate(el => el.getAnimations().every(a => a.playState === 'finished'))` — or wait for the post-animation aria state.
- **Debounce:** If the app debounces input, type and then `await page.waitForResponse(searchEndpoint)` instead of asserting on filtered results immediately.

**DO NOT** add `test.retry(3)` to hide flakiness. Retries are an emergency valve for CI, not a fix.

## 3. Triage decision tree

```
test failed
├── error message says timeout?
│   ├── element appears in later snapshot → TIMING
│   └── element never appears → SELECTOR_DRIFT or real regression
├── error message says "not found" or "strict mode violation"
│   → SELECTOR_DRIFT (re-snapshot to confirm)
├── assertion mismatch?
│   ├── page in expected state otherwise → ASSERTION_MISMATCH (ask user: intent or regression?)
│   └── prior steps already broken → root cause is in prior steps, not the assertion
├── console.error captured?
│   → CONSOLE_ERROR (read the error; usually a real bug)
├── network panel red?
│   → NETWORK_FAIL (check if it's the test's target)
├── passes alone, fails in suite?
│   → STATE_POLLUTION
├── passes on retry?
│   → FLAKY (find the race, don't paper over)
└── none of the above
    → Open the trace, read every step. Don't guess.
```

## 4. Outputs

When triaging for `/e2e:audit`, emit one line per finding in this exact format (so the audit summary can sort/group):

```
<spec_path>:<line>  [<CLASS>]  <one-line summary>
  Root cause: <what the trace shows — specific>
  Fix direction: <what to change — 1-2 sentences>
```

Group findings with identical root cause. If 8 tests fail because one button got renamed, that's ONE finding, not eight. Note the count in the summary.

## 5. Anti-patterns to call out

If you see these in the codebase while triaging, mention them in the audit report (don't auto-fix):

- `test.retry(N)` — masking flakiness
- `test.fixme` without a linked issue — abandoned tests
- `await page.waitForTimeout(...)` — should be replaced
- `.only` left in code — should be caught by `forbidOnly` in CI
- A pile of `test.skip` — what's broken that nobody owns?

These are tech debt signals. The user may not have asked, but they're worth surfacing.
