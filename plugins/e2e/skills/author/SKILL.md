---
name: author
description: Rules for writing Playwright *.spec.ts files that don't flake — selector hygiene, auto-wait discipline, idempotent fixtures, readable trace output. Used by /e2e:write, /e2e:record, and the e2e:spec-writer subagent.
user_invocable: false
---

# e2e:author — Playwright Spec Authoring Rules

You are about to write or modify a Playwright spec. These rules are non-negotiable unless the user explicitly overrides one — they encode lessons that took the Playwright community years to learn.

## 1. Locators

**Priority order — use the first that works:**

1. `page.getByRole(role, { name })` — matches accessibility tree. Survives most refactors.
2. `page.getByLabel(text)` — for form fields with `<label>` association.
3. `page.getByText(text, { exact })` — for prose, headings, links by visible text.
4. `page.getByTestId(id)` — only if the team uses `data-testid` and is committed to keeping them stable.
5. `page.locator('css')` — last resort. Comment why nothing above worked.

**Never:**
- XPath (unless wrapping a known-stable third-party widget)
- CSS class names that look like Tailwind/utility classes (`.bg-blue-500`) — they're transient
- Nth-child indexing into a list without anchoring on a sibling's text

**Always:**
- Chain locators when the element is inside a known parent: `page.getByRole('navigation').getByRole('link', { name: 'Pricing' })`. This survives a copy of the same locator appearing elsewhere on the page.
- Use `{ exact: true }` on text matches when partial matches would be ambiguous.

## 2. Waits

**Never write `page.waitForTimeout(ms)`.** It is always wrong. Replace with one of:

- `await expect(locator).toBeVisible({ timeout })` — wait for the element to appear
- `await expect(locator).toHaveText(value)` — wait for the content to settle
- `await page.waitForURL(predicate)` — wait for navigation
- `await page.waitForResponse(url)` — wait for a specific API call

Auto-wait is built into `expect()` against locators — `await expect(page.getByRole('button')).toBeEnabled()` polls until true or times out. Use it.

The only exception: a documented animation that has no observable end state (no class change, no aria-hidden flip). In that case, comment WHY the timeout is unavoidable.

## 3. Structure

```ts
import { test, expect } from '@playwright/test';

test.describe('<feature or page>', () => {
  test.beforeEach(async ({ page }) => {
    // common setup (navigation, auth state already set via storageState)
  });

  test('<specific behavior>', async ({ page }) => {
    await test.step('<human-readable step>', async () => {
      // ...
    });

    await test.step('<next step>', async () => {
      // ...
    });
  });
});
```

- One `test.describe` per spec file. Name it after the feature or page.
- One `test()` per concrete behavior. Don't bundle "login, add todo, delete todo" into one test — three tests.
- `test.step()` makes the trace.zip readable. Use it for any test with more than 3 actions.

## 4. Assertions

**Assert on what the user sees**, not implementation:

- ✅ `expect(page).toHaveURL('/dashboard')`
- ✅ `expect(page.getByRole('heading', { name: 'Welcome' })).toBeVisible()`
- ✅ `expect(page.getByText('Saved')).toBeVisible()` (toast)
- ❌ `expect(localStorage.getItem('token')).toBeTruthy()` (unless the token's existence IS the feature)
- ❌ `expect(window.__APP_STATE__.user.id).toBe('123')` (testing internals)

Assert console state at the end of every meaningful test:

```ts
const errors: string[] = [];
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
// ... test body ...
expect(errors).toEqual([]);
```

A test that passes while the console is screaming is a test that lies.

## 5. Idempotency

Each `test()` must:
- Set up the state it needs (don't depend on prior test runs)
- Tear down or use unique data (don't leave database rows that affect siblings)
- Use unique identifiers — `const todoText = \`test-todo-${Date.now()}\`` — so reruns don't collide

The Playwright default is `fullyParallel: true`. If a test only passes when run alone, it's broken — fix the test, don't disable parallelism.

## 6. Fixtures over beforeEach for shared setup

For setup that's expensive (login, seeded data), use Playwright fixtures and `storageState`. Logging in inside `beforeEach` of 40 tests = 40 login flows × N seconds = slow + flaky.

The `/e2e:matrix` command handles this for role-based auth. For other expensive setup, define a custom fixture in `tests/fixtures.ts` and import the typed `test` from there.

## 7. Don't suppress real failures

- Don't add `test.fixme(true, 'flaky')` to make a flaky test "pass". Find the race, fix it.
- Don't catch and swallow errors in test code. If you need to test that an error occurs, use `await expect(...).rejects.toThrow(...)`.
- Don't mock the network indiscriminately. Mock only the third-party calls that are out of scope; let your own API run.

## 8. Output discipline

- Spec file path: kebab-case slug describing the behavior, in `tests/`. Example: `tests/login-shows-dashboard.spec.ts`.
- No `console.log` left in committed specs (Playwright has its own trace; logs are noise).
- No `.only` left in committed specs (`forbidOnly: true` in CI config catches this, but don't even author them).

## 9. When in doubt — what does the trace say?

After writing a spec, run it with `--trace=on`:

```bash
npx playwright test path/to/spec.ts --trace=on
```

Open `npx playwright show-trace test-results/.../trace.zip`. The trace shows DOM before/after every action, console, network. If the spec passes but the trace looks weird (silent re-renders, racing network calls, console warnings), tighten it before declaring done.
