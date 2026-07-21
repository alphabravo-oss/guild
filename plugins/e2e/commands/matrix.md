---
description: Generate routes × roles coverage suite with reused storageState (login once per role)
---

The user wants every route × every role tested. Roles config and route source from $ARGUMENTS or sensible defaults.

## Preflight

1. `playwright.config.ts` exists.
2. Smoke specs from `/e2e:crawl` exist at `tests/smoke/` — if not, ask the user to run `/e2e:crawl` first. The matrix builds on the route inventory; don't redo discovery here.
3. `.auth/` is in `.gitignore` — verify, refuse to proceed if missing (auth artifacts must never be committed).

## Determine roles

Look for `e2e.config.json` at the cwd root. If absent, ask the user for the role list and login credentials per role. Default scaffold:

```json
{
  "baseURL": "http://localhost:3000",
  "roles": {
    "anon": null,
    "user": { "username": "test-user@example.com", "password": "..." },
    "admin": { "username": "test-admin@example.com", "password": "..." }
  },
  "loginFlow": {
    "url": "/login",
    "usernameField": "Email",
    "passwordField": "Password",
    "submitButton": "Sign in"
  }
}
```

The `anon` role has `null` credentials — used for unauthenticated traffic. Never invent credentials. If the user hasn't provided them, ask.

## Generate authenticated storageState (once per role)

For each non-anon role:

1. Write `tests/auth/<role>.setup.ts` — a setup spec that logs in and saves `storageState` to `.auth/<role>.json`:

```ts
import { test as setup } from '@playwright/test';
import config from '../../e2e.config.json';

const role = '<role>';
const file = `.auth/${role}.json`;

setup(`authenticate ${role}`, async ({ page }) => {
  const creds = config.roles[role];
  await page.goto(config.loginFlow.url);
  await page.getByLabel(config.loginFlow.usernameField).fill(creds.username);
  await page.getByLabel(config.loginFlow.passwordField).fill(creds.password);
  await page.getByRole('button', { name: config.loginFlow.submitButton }).click();
  await page.waitForURL((url) => !url.pathname.includes('/login'));
  await page.context().storageState({ path: file });
});
```

2. Add a `setup` project to `playwright.config.ts` that runs these before the matrix:

```ts
projects: [
  { name: 'setup', testMatch: /.*\.setup\.ts/ },
  // matrix projects depend on setup
  { name: 'matrix-anon', testMatch: /matrix\/anon\/.*\.spec\.ts/ },
  { name: 'matrix-user', testMatch: /matrix\/user\/.*\.spec\.ts/,
    use: { storageState: '.auth/user.json' }, dependencies: ['setup'] },
  { name: 'matrix-admin', testMatch: /matrix\/admin\/.*\.spec\.ts/,
    use: { storageState: '.auth/admin.json' }, dependencies: ['setup'] },
],
```

Read the existing config first and merge — don't replace the user's project list.

## Generate matrix specs

For each (route, role) pair, emit `tests/matrix/<role>/<slug>.spec.ts`:

```ts
import { test, expect } from '@playwright/test';

test.describe('matrix: <role> @ <route>', () => {
  test('access + render check', async ({ page }) => {
    const response = await page.goto('<route>');
    const status = response?.status() ?? 0;

    // The expected outcome depends on whether <role> should have access.
    // The first run captures observed behavior; the user marks it correct or not.
    test.info().annotations.push({ type: 'observed-status', description: String(status) });

    expect([200, 401, 403, 404]).toContain(status);

    if (status === 200) {
      await expect(page.locator('main, [role="main"]').first()).toBeVisible();
    }
  });
});
```

The matrix is **observation-first** — first run captures what actually happens for each (role, route). The user then reviews and tightens assertions to lock in correct behavior. Don't pretend you know the access policy without the user telling you.

## Run

```bash
npx playwright test tests/matrix/ --reporter=list
```

## Report

For each role:
- Pass count, fail count
- A coverage table: route × role × observed status

Tell the user:
- Review the matrix output and tighten assertions per cell — replace the permissive `[200, 401, 403, 404]` array with the actual expected status for each (route, role) pair
- After tightening, the matrix becomes a regression net against access-control drift

## What you do NOT do

- Do not commit `.auth/*.json` — they contain session tokens
- Do not invent role names or credentials the user didn't provide
- Do not assume the access policy — observe first, ask the user to lock it
