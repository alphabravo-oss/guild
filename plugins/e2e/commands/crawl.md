---
description: Discover every route in the app and generate one smoke spec per route
---

The user wants a smoke test per discoverable route. Base URL is in $ARGUMENTS (defaults to `playwright.config.ts` baseURL).

## Preflight

1. `playwright.config.ts` must exist. If not, route them to `/e2e:init`.
2. Dev server must be reachable. `curl -sI $BASE_URL`.
3. Playwright MCP must be loaded (for the crawl phase).

## Route discovery — two passes, both run

### Pass 1: Static — read the router config

Detect the framework by what's in the repo:

| Framework | Where routes live |
|---|---|
| Next.js (app router) | `app/**/page.{ts,tsx,js,jsx}` — route = path from `app/` minus `/page.*`, with `[param]` → `:param` |
| Next.js (pages router) | `pages/**/*.{ts,tsx,js,jsx}` excluding `_app`, `_document`, `api/` |
| React Router | grep for `<Route path=` and `createBrowserRouter` |
| Vue Router | `router/index.{ts,js}` — read the `routes` array |
| SvelteKit | `src/routes/**/+page.{svelte,ts}` |
| Remix | `app/routes/**/*.{ts,tsx,jsx}` |
| Astro | `src/pages/**/*.{astro,md,mdx}` |

Use Grep/Glob. List every static route. For dynamic routes with params, generate a smoke spec only if the user can give you a sample value — otherwise note them as "dynamic — needs sample data" and skip generation.

### Pass 2: Dynamic — BFS crawl from baseURL via MCP

`browser_navigate` to baseURL, `browser_snapshot`, extract all anchor `href`s pointing to same-origin paths, enqueue. Visit each, repeat. Depth limit 3, total page limit 50 — bail with a count if hit.

Union the two passes. Static catches routes hidden behind auth or feature flags; dynamic catches routes that exist but aren't in the obvious router config (e.g., generated, lazy-loaded). Deduplicate.

## Generate specs

For each route, emit `tests/smoke/<slug>.spec.ts` where slug is the route with `/` → `-` (root → `index`):

```ts
import { test, expect } from '@playwright/test';

test.describe('smoke: <route>', () => {
  test('renders without errors', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    const response = await page.goto('<route>');
    expect(response?.status()).toBeLessThan(400);

    await expect(page.locator('main, [role="main"]').first()).toBeVisible();
    expect(consoleErrors).toEqual([]);
  });
});
```

Three assertions per route:
1. HTTP status < 400
2. A `main` landmark renders (the page actually rendered something, not a blank shell)
3. No console errors during initial render

That's the smoke threshold. Anything more belongs in a per-route spec, not a smoke spec.

## Run the suite

```bash
npx playwright test tests/smoke/
```

Report:
- Total routes discovered (static count, dynamic count, union)
- Pass count, fail count
- For each failure: route + reason from the trace
- Dynamic routes that need sample data (skipped from generation)

## Suggest next steps

If failures exist, suggest `/e2e:audit` for triage. If everything passes, suggest `/e2e:matrix` to expand into role-aware coverage.
