---
description: Launch `playwright codegen` for manual capture of a flow into a *.spec.ts
---

The user wants to record a flow manually with Playwright's recorder. Slug is in $ARGUMENTS.

When to use this vs `/e2e:write`:
- `/e2e:write` — Claude drives the browser via MCP. Good for flows Claude can perform autonomously (clear instructions, deterministic).
- `/e2e:record` — Human drives. Good for flows that are hard to describe in words (drag gestures, file uploads, anything with sensitive credentials Claude shouldn't see).

## Preflight

1. `playwright.config.ts` exists.
2. Dev server is reachable at baseURL.
3. The cwd has access to a display — `playwright codegen` opens a real browser window. If on a headless server, this command does not apply; tell the user and exit.

## Procedure

### 1. Slug + path

Convert $ARGUMENTS to kebab-case. Target: `tests/<slug>.spec.ts`. Refuse to overwrite an existing file — bump to `<slug>-2.spec.ts`.

### 2. Launch codegen

```bash
npx playwright codegen --output tests/<slug>.spec.ts $BASE_URL
```

Where `$BASE_URL` comes from `playwright.config.ts` (read it, don't hardcode). The recorder opens a browser window AND an inspector — the user clicks around, the inspector writes code in real time.

Tell the user:
- "A browser window has opened. Perform the flow you want to capture."
- "Close the browser window when done. Codegen will save the spec automatically."

### 3. Wait for codegen to exit

This is a foreground command — it blocks until the user closes the recorded browser. Don't run it in the background; you need the exit signal.

### 4. Post-process

When codegen exits, read the generated file. It will be functional but rough — codegen produces CSS selectors and literal sleeps, not the hygiene rules in the author skill.

Apply these minimum cleanups (read the author skill for the full list):
- Wrap actions in `test.describe('<flow>', () => { test('<step>', async ({ page }) => { ... }) })` — codegen emits a flat script
- Replace CSS selectors with `getByRole`/`getByLabel` where the accessible name is unambiguous
- Remove any `page.waitForTimeout` — replace with `expect(...).toBeVisible()` waits against the next stable element
- Add at least one `expect()` assertion at the end — codegen captures actions, not assertions

Do NOT rewrite locators where the codegen choice is fine. The recorder picks reasonable selectors most of the time; only edit when there's a hygiene problem.

### 5. Run it

```bash
npx playwright test tests/<slug>.spec.ts
```

If it fails: the recording captured a timing-dependent flow. Apply the audit triage (`/e2e:audit` logic, but inline since it's one spec). Report and suggest a fix.

If it passes: report path and runtime. Suggest `/e2e:audit` to check the full suite still passes.

## What you do NOT do

- Don't perform the recording yourself — that's `/e2e:write`. Record is for human-driven capture.
- Don't filter or redact the recorded credentials. If the user typed sensitive data into codegen, that's on them — but tell them to scrub before committing.
