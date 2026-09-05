---
name: sight
description: Deep browser-based UI audit. Clicks every button, fills every form, opens every dropdown, reads every console log — like a scrutinizing QA engineer. Supports headed and headless. Used as foundry's F2 SIGHT verification stream and as a standalone QA tool.
user_invocable: true
---

> **Foundry integration:** This skill is invoked by Foundry's F2 INSPECT phase as the `sight` stream. SIGHT runs in the main thread (Playwright MCP requirement). When run from F2, findings are filed through the `Foundry-Defect` door and land in `foundry-archive/{run}/defects.json`; sight never writes that file itself. When run standalone, findings are written as a report. Skip with `--no-ui`.

# /foundry:sight — Deep Browser UI Audit

Sight uses Playwright MCP to **deeply exercise** a web application. It doesn't just visit pages — it clicks every button, fills every form, opens every dropdown, expands every accordion, triggers every modal, and watches what happens. It reads console logs, tracks network requests, and documents everything that's wrong.

Think: a meticulous QA engineer or a frustrated user trying to break things.

Supports **headed mode** (GUI) and **headless mode** (CI/remote).

## Prerequisites

- **Playwright MCP** configured in `.mcp.json` with devtools enabled:

  **Headed (default — GUI available):**
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

  **Headless (no GUI / CI / remote server):**
  ```json
  {
    "mcpServers": {
      "playwright": {
        "command": "npx",
        "args": ["@playwright/mcp@latest", "--headless", "--caps", "vision,devtools"]
      }
    }
  }
  ```
- The target page must be accessible (running locally or deployed)

## Invocation

| Command | Behavior |
|---------|----------|
| `/foundry:sight <url>` | Deep audit, document findings |
| `/foundry:sight <url> --parity <reference>` | Compare against a reference design or URL |

## Core Principle: Exercise Everything AND Verify Outcomes

**DO NOT** just visit a page, glance at it, and move on. For every page:

0. **Find ALL pages** — don't rely only on visible navigation. Read the app's router config
   to discover every defined route and visit it, even if it's not linked in the UI.
1. **Read the DOM snapshot** — understand every element on the page
2. **Interact with every interactive element**:
   - Buttons: click them. What happens? New console errors? Network calls? Modal?
   - Links: where do they go? Do they 404?
   - Form fields: type into them. Do they validate? What happens on submit?
   - Dropdowns/selects: open them. Are options populated? Do they filter correctly?
   - Toggles/switches: flip them. Does state change? API call fire?
   - Tables: check every column. Are cells empty? Do sort headers work? Does pagination work?
   - Tabs: click each tab. Does content load? Errors?
   - Search boxes: type a query. Does it filter? Debounce correctly?
   - Modals/dialogs: trigger them. Do they open/close cleanly? Can you escape?
   - Accordions/expandable sections: expand them all
   - Action menus (... buttons): click them. What options appear?
3. **Verify the outcome actually rendered** — clicking "View Manifest" must show a manifest,
   not a blank panel. Every action must produce a visible, correct DOM change. Silent no-ops,
   empty renders, stale content, and stuck spinners are all findings.
4. **Check console after every interaction** — new errors? warnings?
5. **Check network after every interaction** — failed requests? slow responses?
6. **Screenshot before and after** significant interactions
7. **Try edge cases**:
   - Submit empty forms
   - Enter invalid data (special chars, extremely long strings, SQL injection patterns)
   - Click buttons rapidly
   - Use keyboard navigation (Tab, Enter, Escape)
8. **Scenario enumeration** — for each page, don't just test what's visible. Think about
   what a real user would TRY to do:
   - "I see a list of items — can I sort them? Filter them? Search them? Paginate?"
   - "I created an item — can I edit it? Delete it? Duplicate it? Export it?"
   - "This form has 5 fields — what if I fill only 1? What if I fill all with garbage?"
   - "I'm on the detail page — what if I go back? What if I refresh? Bookmark?"
   - "There are 0 items — is there an empty state? Or just a blank table?"
   - "There are 100 items — does pagination work? Performance OK?"
   **Count the scenarios you tried vs the scenarios a user would reasonably attempt.**
   If you tried 5 out of 20 reasonable scenarios, go back and try more. The goal is
   EXHAUSTIVE scenario coverage, not "a few interactions per page."
9. **Verify data roundtrips**: after creating or modifying data through a form,
   navigate to the page that DISPLAYS that data and verify the values match.
   This catches serialization bugs, missing fields, and data loss:
   1. Fill and submit a create form (e.g., "Create User" with name "Test User")
   2. Navigate to the list/detail page that shows the created item
   3. Verify EVERY field value matches what was submitted
   4. If values don't match or the item doesn't appear: finding (data loss)
   5. Check edge cases: special characters, long strings, empty optional fields

## Task

**CRITICAL: This skill requires the Playwright MCP browser tools (`mcp__playwright__*`).
All browser interaction MUST go through these tools — not code reading, not curl, not
fetch. You must actually navigate pages, click elements, and verify outcomes in a real
browser session.**

Required Playwright MCP tools:
| Tool | Purpose |
|------|---------|
| `mcp__playwright__browser_navigate` | Go to URLs |
| `mcp__playwright__browser_snapshot` | Read DOM / accessibility tree |
| `mcp__playwright__browser_click` | Click buttons, links, tabs, checkboxes |
| `mcp__playwright__browser_fill_form` | Type into text inputs, textareas |
| `mcp__playwright__browser_select_option` | Choose dropdown/select values |
| `mcp__playwright__browser_press_key` | Keyboard: Enter, Escape, Tab |
| `mcp__playwright__browser_take_screenshot` | Capture visual state |
| `mcp__playwright__browser_console_messages` | Read console errors/warnings |
| `mcp__playwright__browser_network_requests` | Check failed API calls |
| `mcp__playwright__browser_hover` | Hover for tooltips, menus |
| `mcp__playwright__browser_evaluate` | Run JS in the page context |
| `mcp__playwright__browser_wait_for` | Wait for elements, navigation |
| `mcp__playwright__browser_tabs` | Manage browser tabs |
| `mcp__playwright__browser_resize` | Test responsive layouts |
| `mcp__playwright__browser_handle_dialog` | Accept/dismiss alert/confirm |

If any `mcp__playwright__*` call returns "unknown tool", Playwright MCP is not
configured. Run `/foundry:setup` to install all foundry prerequisites.

**BANNED ACTIONS — These are NOT UI auditing:**
- Do NOT use Bash to run tsc, eslint, npx, curl, or any CLI tool
- Do NOT read source code files (.tsx, .ts, .jsx, .go, .py) to "audit" the UI
- Do NOT use Grep or Glob to search source code
- Do NOT write a report based on code reading — you must SEE the rendered UI
- If you catch yourself running Bash commands instead of Playwright tools, STOP
  and switch to browser-based testing immediately

The ONLY way to audit a UI is to open it in a browser and interact with it.
Reading the source code that generates the UI is a logical audit, not a UI audit.

### Step 0: ENSURE SERVERS ARE RUNNING

Before navigating, verify that both the frontend AND backend are accessible.

**Frontend:** Try to reach the target URL. If it fails:
- Detect the dev server command from `package.json` scripts (`dev`, `start`, `serve`) or project conventions (`go run`, `python manage.py runserver`, etc.)
- Start it as a background process and wait for HTTP 200

**Backend:** Check if a separate backend/API server exists and is running:
1. Look for backend indicators in the project (Go main.go, Node server.js, Python
   manage.py, `backend/` or `server/` or `api/` directory)
2. Find the backend port from code/config/env files
3. Try to reach `http://localhost:<port>` — if it responds, backend is running
4. If NOT running and `--skip-start-backend` was NOT passed:
   - Start the backend as a background process
   - Wait for it to be ready (30s timeout)
   - If it fails to start, log a warning and continue (the audit will capture
     the resulting network errors as real findings)
5. If `--skip-start-backend` was passed: skip, assume user manages the backend

**Why:** A UI audit without a running backend produces hundreds of false network
errors. Every API call fails, every form submission fails, every data table is
empty. These aren't UI bugs — they're a missing backend.

### Step 1: CONNECT — Navigate and Authenticate

**Your VERY FIRST tool call in this step MUST be:**
```
mcp__playwright__browser_navigate  url: "<target_url>"
```
If this fails with "unknown tool", STOP and report that Playwright MCP is not
available. Do NOT fall back to Bash/curl/code reading.

1. Navigate to the target URL using `mcp__playwright__browser_navigate`
2. Take a screenshot: `mcp__playwright__browser_take_screenshot`
3. Read the DOM: `mcp__playwright__browser_snapshot`
4. If a login page: fill credentials with `mcp__playwright__browser_fill_form`
   and submit with `mcp__playwright__browser_click`
5. After auth, capture baseline:
   - Screenshot: `mcp__playwright__browser_take_screenshot`
   - DOM snapshot: `mcp__playwright__browser_snapshot`
   - Console messages: `mcp__playwright__browser_console_messages`
   - Network requests: `mcp__playwright__browser_network_requests`
4. Note the navigation structure — build a sitemap of all pages to visit
5. **Route manifest discovery** — cross-reference navigation with the app's actual routes:
   - Search for router config files: React Router (`createBrowserRouter`, `<Route`, `routes.ts`,
     `routes.tsx`, `router.ts`), Next.js (`app/` or `pages/` directory), Vue Router (`router/index.ts`),
     Angular (`app-routing.module.ts`, `*.routes.ts`), SvelteKit (`src/routes/`)
   - Extract all defined route paths from the router config
   - Compare against the navigation-discovered sitemap
   - **Any route in the config but NOT in the navigation must still be visited** by direct URL navigation
   - Log which routes were discovered via nav vs. direct URL in the audit report
   - If no router config is found, log a warning and rely on navigation-only discovery

### Step 2: DEEP AUDIT — Page by Page

For **each page** reachable from navigation:

#### 2a: OBSERVE
1. Navigate to the page
2. Screenshot the initial state
3. Read the full DOM snapshot — understand every element
4. Collect console messages — note any new errors from navigation
5. Collect network requests — note any failures from page load

#### 2b: EXERCISE EVERY ELEMENT
Walk through the DOM snapshot top-to-bottom. For each interactive element:

**Buttons:**
- Click it
- Check: did a modal open? Did a network call fire? Did console log an error?
- Screenshot the result
- If it opened a dialog/modal, test the modal's contents too (forms inside, cancel/confirm buttons)
- Close/dismiss and verify clean return to previous state

**Form fields (text inputs, textareas, number inputs, spinbuttons):**
- Check: is there a current value? Placeholder?
- Type a valid value — does it accept it?
- Type invalid input — does validation fire? What's the error message?
- Clear it — does it show a required error?
- Submit the form — check network request and response, check console

**Dropdowns/selects/comboboxes:**
- Click to open — are options populated?
- Select each option — does it filter/change the view?
- Check console/network after selection

**Tables:**
- Count rows vs what pagination claims
- Check every column — are any cells empty that shouldn't be?
- Click column headers — do they sort?
- Check action buttons on each row (edit, delete, ...) — click at least one
- Verify pagination controls (previous, next, page numbers)

**Tabs:**
- Click each tab — does content load?
- Check console/network after each tab switch
- Screenshot each tab's content

**Search/filter inputs:**
- Type a query that should match existing data
- Type a query that should match nothing — does empty state show?
- Clear the search — does it reset?

**Toggle/switches:**
- Note initial state (on/off)
- Toggle — does it fire an API call? Update visually?
- Toggle back — does it revert?

**Navigation links:**
- Click and verify the page loads (not 404)
- Check breadcrumb trail is correct

**Action menus (kebab/... buttons):**
- Click to open
- Note all options
- Click each option (or at least one representative one)
- Screenshot

#### 2b.5: OUTCOME VERIFICATION — Did It Actually Work?

**After every interaction, verify the expected outcome actually rendered.** A button that
silently does nothing, a panel that opens empty, a form that submits but shows no result —
these are real bugs even if the console is silent and the network returned 200.

**Protocol for every interaction:**

1. **Before**: Capture DOM snapshot (accessibility tree) as the "before" state
2. **Perform**: Execute the interaction (click, submit, toggle, etc.)
3. **After**: Capture DOM snapshot as the "after" state
4. **Verify**: Compare before/after — the DOM MUST have changed meaningfully

**What "meaningfully changed" means per interaction type:**

| Action | Expected Outcome — Verify This |
|--------|-------------------------------|
| Click "View {X}" / "Show {X}" | Content panel/modal appeared AND contains data related to {X} — not empty, not a spinner stuck forever, not a blank panel |
| Click "Edit" / "Edit {X}" | Edit form appeared with pre-populated fields matching the item being edited |
| Click "Delete" / "Remove" | Confirmation dialog appeared, OR item was removed from the list/table |
| Click "Create" / "New" / "Add" | Creation form or wizard appeared with empty/default fields |
| Click "Deploy" / "Run" / "Execute" | Status indicator changed (progress bar, spinner, status badge update) |
| Submit a form | Success message appeared, OR page redirected, OR the created/updated item is visible in a list |
| Toggle a switch | Visual state flipped AND (if applicable) the API call confirmed the change |
| Open a dropdown | Options list appeared AND is populated (not empty) |
| Select a dropdown option | The selected value is displayed AND any dependent UI updated |
| Click a tab | Tab content area changed to show the tab's content — not empty, not the previous tab's content |
| Click a table sort header | Row order changed (or stayed same if already sorted that direction) |
| Click pagination | Different rows are shown, page indicator updated |
| Expand an accordion | Content section appeared with actual content inside |
| Click a navigation link | Page changed, URL updated, new content loaded |
| Search/filter | Results updated to match the query, OR empty state shown for no matches |

**Failure classifications:**

- **Silent no-op**: Interaction produced zero DOM change and zero console/network activity.
  → Finding: `"{element}" does nothing when clicked — no visible response, no console, no network`
- **Empty render**: A panel/modal/section appeared but contains no meaningful content.
  → Finding: `"Clicking {action} opens {container} but it is empty — expected {what should appear}"`
- **Stale content**: Content appeared but doesn't match the context (e.g., clicking "View Manifest"
  for Item A shows Item B's manifest, or shows hardcoded/placeholder data).
  → Finding: `"Content rendered after {action} does not match context — expected {X}, got {Y}"`
- **Stuck loading**: A spinner/skeleton appeared but never resolved (wait 5 seconds max).
  → Finding: `"Loading state after {action} never resolved — spinner visible for 5+ seconds"`
- **Partial render**: Some expected content appeared, but key fields are missing or show
  "undefined", "null", "NaN", "[object Object]", or empty strings.
  → Finding: `"{element} rendered with missing/broken data: {field} shows {value}"`

**This step is NOT optional.** Every interaction in Step 2b must have its outcome verified.
Checking console + network is necessary but NOT sufficient — the DOM must prove the action worked.

**Backend verification for mutations:**
After any CREATE, UPDATE, or DELETE action that shows success in the UI:
1. Navigate AWAY from the current page (go to the list page or another page)
2. Navigate BACK to verify the change persisted — is the item still there? Updated? Gone?
3. If the app has a detail view, navigate to it and verify ALL fields match
4. If the API is accessible, use `mcp__playwright__browser_evaluate` to make a fetch
   call and verify the backend actually has the data:
   ```javascript
   await fetch('/api/items/' + id).then(r => r.json())
   ```
5. If the data doesn't persist across navigation, that is a `LIVE` finding — you
   drove the flow and observed the wrong result: the UI showed success but the
   backend didn't actually save

This catches the most insidious bug class: forms that show a success toast but don't
actually call the API, or APIs that return 200 but don't write to the database.

#### 2c: CHECK RESPONSIVENESS
For pages with data tables or complex layouts:
- Resize to 375px (mobile) — screenshot
- Resize to 768px (tablet) — screenshot
- Resize back to 1280px (desktop)
- Note overflow, clipping, or broken layouts

#### 2d: RECORD
After exercising every element on the page, record:
- All console errors/warnings accumulated
- All failed network requests
- All visual issues found
- All functional issues found
- All accessibility issues found
- Screenshots of every significant state

### Step 2e: PERSIST CONSOLE LOGS

**After auditing all pages**, write ALL captured console messages to a persistent file:

When run from foundry: `foundry-archive/{run}/sight/console-logs-cycle-{N}.md`.
When run standalone: `sight-reports/console-logs-{timestamp}.md`.

```markdown
# Console Logs — Iteration {N}
Date: {date}
URL: {base url}
Pages visited: {N}
Total console messages: {N}
Errors: {N} | Warnings: {N} | Info: {N}

## Errors (grouped by unique message)

### CE-1: {short error summary}
- **Page:** {url path where first seen}
- **Trigger:** {what interaction caused it — e.g., "clicking Deploy button"}
- **Message:** `{full console error message}`
- **Stack:** `{stack trace if available}`
- **Frequency:** {N} occurrences across {N} pages
- **User impact:** {what breaks — e.g., "Deploy button does nothing"}
- **Also seen on:** {other pages where same error appears}

### CE-2: ...

## Warnings (grouped by unique message)

### CW-1: {short warning summary}
- **Page:** {url path}
- **Message:** `{full warning}`
- **Frequency:** {N}
- **Type:** React warning | deprecation | other

## Info (only notable — skip routine framework logs)

### CI-1: {notable info message}
```

This file is persistent and referenced from the audit report. Console errors that
break user-facing functionality become findings in the foundry defect list.

### Step 3: DOCUMENT — Write the Audit Report

When run from foundry: `foundry-archive/{run}/sight/audit-cycle-{N}.md`.
When run standalone: `sight-reports/audit-{timestamp}.md`.

Use this structure:

```markdown
# UI Audit: {url}
Date: {date}
Viewport: {width}x{height}
Pages audited: {N}
Elements exercised: {N}
Total interactions: {N}

## Summary
- Findings driven (`LIVE`): {N}
- Findings derived (`LATENT`): {N}
- Console errors: {N} (unique), {N} (total)
- Network failures: {N}
- Accessibility: {N}
- Pages broken: {N}/{total}

## Console & Runtime Errors
### E1: {error message summary}
- **Type**: error | warning | uncaught exception | unhandled rejection
- **Message**: {full message}
- **Stack**: {stack trace if available}
- **Trigger**: {what interaction caused this — e.g., "clicking Delete button on row 3"}
- **Page**: {which page}
- **Frequency**: {how many times it fired}
- **Impact**: {what user-facing behavior this causes}

## Network Failures
### N1: {failed request}
- **URL**: {request URL}
- **Method**: {GET/POST/etc}
- **Status**: {status code or error type}
- **Trigger**: {what interaction caused this}
- **Request body**: {summary of what was sent, if visible}
- **Impact**: {what breaks}

## Functional Issues
### F1: {title}
- **Page**: {page URL}
- **Element**: {what element, CSS selector or description}
- **Action**: {what you did — clicked, typed, submitted}
- **Expected**: {what should happen}
- **Actual**: {what actually happened}
- **Console**: {any console errors triggered}
- **Network**: {any network calls triggered}

## Visual Issues
### V1: {title}
- **Page**: {page URL}
- **What**: {description}
- **Where**: {CSS selector or visual location}
- **Viewports affected**: {which breakpoints}

## Data Issues
### D1: {title}
- **Page**: {page URL}
- **What**: {empty columns, wrong counts, missing labels, stale data}
- **Element**: {table/cell/badge description}
- **Expected**: {what should be displayed}
- **Actual**: {what is displayed}

## Accessibility Issues
### A1: {title}
- **Rule**: {WCAG rule violated}
- **Element**: {selector}
- **Fix**: {what to change}

## Route Coverage
- Routes from router config: {N}
- Routes discovered via navigation: {N}
- Routes discovered via direct URL only: {N}
- Routes unreachable (404 or auth-blocked): {N}
- **Coverage: {N}% of defined routes visited**

| Route | Source | Status |
|-------|--------|--------|
| /dashboard | nav | OK |
| /settings/advanced | router config (direct URL) | OK |
| /admin/debug | router config (direct URL) | 403 — auth required |

## Page-by-Page Detail

### {Page Name} — {url path}
**Status**: OK | FUNCTIONAL (has issues) | BROKEN | 404
**Discovery**: navigation | router config (direct URL)
**Console errors on load**: {N}
**Network failures on load**: {N}

**Elements exercised:**
- [ ] {button/link/form} — **Outcome**: {OK: expected content rendered | FAIL: issue ref}
- [ ] {button/link/form} — **Outcome**: {OK: expected content rendered | FAIL: issue ref}
...

### {Next Page} — {url path}
...
```

### Step 3.5: SUGGEST — UX Improvement Proposals

**This step runs when invoked as the foundry SIGHT stream.** In standalone
mode, this step is skipped unless `--suggest` is passed.

While auditing, actively identify opportunities to improve the user experience.
Don't just document what's broken — propose what would make it better.

#### What to Suggest

- **Loading states**: Async operations without spinners/skeletons
- **Error feedback**: Failed API calls with no visible error message
- **Confirmation dialogs**: Destructive actions (delete, reset) with no confirmation
- **Empty states**: Lists/tables that show nothing when empty (no "no data" message)
- **Form validation**: Missing inline validation, no character counts, no format hints
- **Keyboard navigation**: Common actions that can't be triggered via keyboard
- **Breadcrumbs**: Deep pages with no way to navigate back to parent
- **Progress indicators**: Multi-step workflows without step progress
- **Undo support**: Destructive actions with no undo capability

#### Feasibility Check — Three Layers

For each suggestion, verify it can actually be implemented:

**Layer 1 — Spec check:**
Read the active foundry spec (the `--spec` path passed to `/foundry:start`, recorded in `foundry-archive/{run}/state.json`).
- Does the spec mention or imply this capability?
- Is it aligned with the spec's intent?
- Result: `spec_supported: yes | no | ambiguous`

**Layer 2 — Codebase check:**
Search the backend code for supporting infrastructure.
- Are there route definitions / handler functions for the needed data?
- Do service methods exist that could provide the data?
- Does the data model include the needed fields?
- Result: `code_supported: yes | no | partial`

**Layer 3 — Runtime check:**
If the app is running, test the relevant endpoint.
- Make a test request to the API endpoint
- Does it return the expected data?
- Result: `runtime_supported: yes | no | error | untested`

#### Classification

The three layers above are what split a suggestion, and the split is by
EVIDENCE — what the running application let you reach — never by an estimate of
how much work a fix would be. A grade for how much a fix is worth has nothing
left to decide here, because every defect this stream files gets fixed.

**A finding** — the improvement is reachable in the application as it stands, so
a GRIND cycle can repair it without new surface. Files as a defect in Step 4,
carrying a `tier` like every other finding.
- `spec_supported: yes | ambiguous`, `code_supported: yes | partial`, and the
  runtime layer returned the data the change needs
- Examples: loading spinners, error toasts, empty state messages,
  confirmation dialogs, inline form validation

**A proposal** — a layer came back unsupported, so there is no instance in the
running application to drive and nothing a GRIND cycle could repair without
building new surface first. Goes to the suggestion backlog file for a human to
decide on, and NEVER into the defect ledger.
- `code_supported: no` — no route, no handler, no field to read
- Examples: a new dashboard page, export functionality, a notification system

A proposal's `Effort estimate` below is an attribute of a user-facing proposal
that a human reads while deciding whether to ask for it. It is not a `tier`, it
is not recorded on any filing, and it routes nothing.

#### Output

**Findings** → added to the audit report as `s-N` items (lowercase). When
invoked as foundry SIGHT, each is filed as a defect in Step 4 through
`Foundry-Defect`, carrying a `tier` like every other finding this stream
reports, for the next GRIND cycle.

**Proposals** → written to the suggestion backlog file at
`foundry-archive/{run}/sight/suggestion-backlog.md` (or `sight-reports/suggestion-backlog.md` standalone). Format:

```markdown
# Suggestion Backlog: {url}
Date: {date}
Total suggestions: {N}
Filed as defects: {N}
Awaiting user approval: {N}

## Pending Proposals (requires user approval)

### S-1: {title}
- **Page**: {url}
- **Suggestion**: {what to add/change}
- **Feasibility**:
  - Spec: {yes/no/ambiguous}
  - Codebase: {yes/no/partial}
  - Runtime: {yes/no/error/untested}
- **Effort estimate**: {small/medium/large}

## Filed as Defects

### s-1: {title}
- **Page**: {url}
- **What was filed**: {description}
- **Tier**: {LIVE | LATENT}
- **Cycle**: {which foundry cycle it was filed in}
```

The backlog accumulates across INSPECT cycles and is presented in the final
foundry report (F6: DONE).

### Step 4: REPORT

**Standalone mode:**
- Present the audit report to the user. Done.

**Foundry SIGHT stream mode (READ-ONLY):**
- **DO NOT fix findings. DO NOT spawn agents. This phase is READ-ONLY.** Fixes happen in F3 GRIND.
- Convert findings into defect entries via the foundry MCP `Foundry-Defect` tool (one call per finding), each carrying `tier`, `defect_class`, `target_kind` and — on a `LATENT` filing — `reproduction_attempted`, per the filing rules below.
- **Never write `foundry-archive/{run}/defects.json` yourself.** That file is the server's. A hand-written entry reaches the ledger without passing the tier check, the class check or the `SECURITY_PROPERTY_CLAIM` denylist, so a finding no door ever saw is indistinguishable there from one that was refused nothing — which is the whole guarantee those doors exist to give. Outside an MCP session you have no filing door at all: write the audit report and stop, and never hand-edit the ledger to stand in for one.
- Mark the stream complete via the foundry MCP `Foundry-Stream` tool with `stream='sight'`, `cycle=<the run's current cycle, from Foundry-Next>` and `items_checked=<count>`. All three are REQUIRED — a call omitting any one is rejected at the MCP boundary and this stream records no coverage for the cycle at all. The roll-up is keyed by the server's own cycle counter; the value you pass is retained on the record for audit only.
- **You record your own stream; the lead only confirms the record exists.** Pass
  `items_total` and `findings_count` beside the three required arguments — the pair is
  what turns "this stream ran" into "this stream covered N of M routes", and a record
  carrying only `items_checked` reports a numerator with no denominator. A second call
  for the same stream and cycle REPLACES the first, names in `replaced` what it
  replaced, and keeps every record under `records[]`, so a second crawl after a login
  fix corrects the cycle's coverage rather than doubling it. Nobody records on your
  behalf: a crawl that ends without the call leaves the cycle reading as no UI coverage
  at all.
- File every UX FINDING as a defect for GRIND, with a `tier`, exactly as you file anything else this stream reports. Proposals stay in the backlog file and never enter the defect ledger. Nothing here routes on how large a fix looks.
- Flow: SIGHT audit (you are here) → INSPECT aggregation → GRIND fixes → next cycle.

#### Filing rules

These are the rules every defect-filing stream carries, word-identically. SIGHT
files into the same ledger through the same doors, so it holds the same rules —
a browser audit is a different lens on the code, not a different contract with
the defect ledger.

- **Name the class when instances share a root cause.** Three HOLLOW verdicts behind one missing middleware are one class, not three unrelated defects — put the shared root cause in each record's `class` field, spelled identically across every instance (`Foundry-Defect` takes it as `defect_class`; `Foundry-Sync` reads it as `class`). A class that draws new defects for three consecutive cycles escalates to a single structural-fix packet, and that only fires if you named it — `systemic_patterns` is your prose summary and nothing downstream consumes it. Name a class on EVERY defect, including one that genuinely stands alone — a single-instance class is still a class, and `Foundry-Defect` and `Foundry-Sync` refuse a filing whose `class` is empty. Never invent a class to bundle findings that do not share a cause.
- **No severity classification.** **No severity tiers.** The work-effort grade is banned by name — no `minor`, no `major`, no `critical`, no `severity`, no `priority`, no `impact`, and no fresh spelling invented next cycle — because every defect gets fixed and a grade for how much a fix is worth has nothing left to decide. Grade a finding by whether you actually drove it or only derived it from a scan, and never by how much work it would take to fix: the first is the `tier` axis the next rule makes required, the second stays abolished. `tier` is evidence, not effort, and it displaces nothing below it — `classification` still decides the channel a finding goes down and `target_kind` still rides on every filing. No exceptions, no deferrals, no "this one is only cosmetic."
- **Set `tier` on every filing; the stream that files the defect is the one that sets it.** `tier` is a closed two-member vocabulary declared once at `plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py#DEFECT_TIERS` — read the members there and never re-type them anywhere else. `LIVE` means you drove the door and observed the wrong result, and the description names both the door and the result. `LATENT` means you derived the finding and found no reachable instance, and that filing MUST carry a `reproduction_attempted` statement naming what you drove and what it found ("AST sweep of both roots finds 0 sites"); `Foundry-Defect` and `Foundry-Sync` refuse a `LATENT` filing without one. A security-property claim can NEVER be `LATENT` — that filing is refused naming the denylist class `SECURITY_PROPERTY_CLAIM` and writes a tripwire record, so a claim that a security property is broken is one you drive and file `LIVE`, or one you do not file at all. Both tiers are defects, both get fixed, and `tier` buys you no discretion over anything else. No exceptions, no deferrals, no "I could not reproduce it, so it is probably fine."
- **Comment-prose findings are observations, not defects.** A drifted line number in a cite, a count stated in prose, a direction word ("above", "below", "the following"), an enumeration that no longer matches the thing it enumerates — that class is comment prose. Record it in the run's `observations.json` ledger, never in the `defects` array; `Foundry-Defect` and `Foundry-Sync` refuse it as a defect server-side. This is a channel, not a severity tier, and it buys you no discretion over anything else.
- **Declare `target_kind` on every filing.** That refusal is not automatic — it fires only when your call DECLARES what the finding is about: `target_kind: "comment"` when the verdict concerns a code comment, otherwise what the subject really is (`code`, `test`, `config`, `doc`). Omit the field and the server has nothing to judge, so a line-drift finding is accepted into `defects.json` and the split above did nothing. Every `Foundry-Defect` and `Foundry-Sync` call carries it, on every verdict, including the ones you are certain about.

#### The shape you file

```json
{
  "defects": [
    {
      "id": "F-1",
      "type": "BROKEN",
      "page": "/projects/42/settings",
      "element": "Save button (button[type=submit])",
      "description": "Clicking Save posts the form and the page returns to its pre-edit values with no error shown. Console records `PATCH /api/projects/42 422` and the response body names `slug` as invalid; nothing surfaces in the UI.",
      "class": "form-submit-swallows-the-server-error",
      "tier": "LIVE",
      "target_kind": "code",
      "evidence": "console-logs-cycle-3.md#CE-1; screenshot projects-42-settings-after-save.png"
    },
    {
      "id": "F-2",
      "type": "MISSING",
      "page": "/projects/42/exports",
      "element": "Export list (empty state)",
      "description": "The export list renders nothing at all when a project has no exports — no row, no message, no illustration — so an empty list is indistinguishable from a list that failed to load.",
      "class": "list-views-ship-without-an-empty-state",
      "tier": "LATENT",
      "target_kind": "code",
      "reproduction_attempted": "Seeded a project with zero exports and loaded the route in the browser; the list rendered empty with no network error and no console output, so there was no failing request to drive and no reachable instance of the load failure this would otherwise be confused with",
      "evidence": "screenshot projects-42-exports-empty.png"
    }
  ]
}
```

`class` is required on every defect, including one that stands alone — a single-instance class is still a class, and the filing doors refuse an empty one. `tier` is required on every defect too: `LIVE` when you drove the element in the browser and observed the wrong result, `LATENT` when you derived the finding and found no reachable instance, in which case `reproduction_attempted` rides beside it as the second entry above shows. Spell the class identically on every instance — escalation counts a class across cycles by exact string, so a near-miss spelling reads as two unrelated classes and never escalates.

## Display Mode

The foundry installer configures Playwright MCP appropriately for the environment:

| Environment | Mode | Reason |
|------------|------|--------|
| macOS | Headed | GUI always available |
| Linux + DISPLAY/Wayland | Headed | Display server detected |
| Linux + SSH (no X11) | Headless | No display forwarding |
| WSL without WSLg | Headless | No native GUI |
| CI (GitHub Actions, GitLab, etc.) | Headless | CI environment |

Override with `--headless` or `--headed` flags.

## Inputs and outputs

| Input | Output |
|---|---|
| Live URL + credentials | Audit report (`foundry-archive/{run}/sight/` or `sight-reports/`) |
| Reference design/URL (parity mode) | Defect entries (foundry mode) |
| Playwright MCP (browser + devtools) | Console log file alongside the audit report |
| Active foundry spec (for suggestion feasibility) | Suggestion backlog (foundry mode only) |
| Foundry run state (cycle, target URL) | `Foundry-Stream` completion record (foundry mode) |
| Prior console logs (check regressions) | |
