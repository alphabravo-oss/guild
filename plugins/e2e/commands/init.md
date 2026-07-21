---
description: Install Playwright + register Playwright MCP + scaffold tests/ in the current project
---

The user wants to initialize Playwright E2E testing in the current project.

## Preflight (read before doing anything)

1. Run `pwd` — confirm you are in the project the user wants to set up. If this is the guild repo itself (cwd ends in `guild`), STOP and ask the user which app to set up — this plugin is not meant to test itself.
2. Check for `package.json` at the cwd root. If missing, ask whether to `npm init -y` or whether they want a different workspace root.
3. Check for existing `playwright.config.ts` / `playwright.config.js`. If present, STOP and ask: append-to-existing, or abort? Never overwrite without explicit ack.
4. Check for existing `.mcp.json`. If present and already has a `playwright` server entry, skip the MCP step — don't clobber their config.

## Steps

### 1. Install Playwright

```bash
npm install --save-dev @playwright/test
npx playwright install
```

Both must succeed. `playwright install` downloads Chromium/Firefox/WebKit — it's a multi-hundred-MB step; tell the user it may take a minute.

### 2. Write `playwright.config.ts`

Use this generic template — works for any framework. Do NOT bake in Next.js/Vite assumptions unless the user has confirmed the stack.

```ts
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['html', { open: 'never' }], ['list']],
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
```

Single browser project by default — add Firefox/WebKit only when the user asks. Multi-browser is a 3x cost multiplier for diminishing return.

### 3. Register Playwright MCP

If `.mcp.json` does not exist, create it with:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest", "--caps", "vision,devtools"]
    }
  }
}
```

If `.mcp.json` exists but has no `playwright` server, merge the `playwright` entry in (preserve all other servers).

Default mode is HEADED (no `--headless` flag) per plugin convention — Claude needs to watch the browser when authoring specs. Users on CI/headless servers can swap to:

```json
"args": ["@playwright/mcp@latest", "--headless", "--caps", "vision,devtools"]
```

### 4. Scaffold `tests/`

```bash
mkdir -p tests
```

Do NOT generate any sample specs. The `/e2e:write` command produces real specs; sample files just create maintenance debt.

### 5. Update `.gitignore`

Append (only if missing):

```
# Playwright
test-results/
playwright-report/
.auth/
```

`.auth/` is for `storageState` files written by `/e2e:matrix`. Never commit them — they contain session tokens.

### 6. Verify

```bash
npx playwright test --list
```

Should run without error and report `0 tests in 0 files`. If it errors, the config is wrong — diagnose before declaring success.

## Report

When done, tell the user:
- What got installed (versions if you have them)
- Whether `.mcp.json` was created or merged
- That they must **restart Claude Code** for the MCP server to load
- Next step: `/e2e:write <flow description>` once the dev server is running and they've restarted

Do NOT auto-start their dev server. That's their call.
