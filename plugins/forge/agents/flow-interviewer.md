---
name: flow-interviewer
description: V3 brownfield R2 methodology reference. IMPORTANT — this document is a METHODOLOGY REFERENCE, NOT a spawned subagent. V3 R2 runs in the main Claude thread (see forge/commands/plan.md §R2 V3 override) because subagents cannot call AskUserQuestion. Do NOT spawn this file as a subagent — without AskUserQuestion the spawned agent has no way to ask the user and silently falls back to forced decisions.
tools: Read, Grep, Glob, Bash, Write, Edit, AskUserQuestion
model: opus
effort: high
---

# Flow-Interviewer — METHODOLOGY REFERENCE

This document describes the brownfield V3 R2 interview methodology. The main Claude thread follows this methodology directly — it does NOT spawn this document as a subagent. Subagent runtimes have no `AskUserQuestion`, so a spawned subagent cannot conduct the interactive node-by-node interview and silently falls back to forced decisions. The methodology must run in the main thread (the session that ran `/forge:plan`); see `forge/commands/plan.md` §R2 V3 override for the executable procedure.

Input: a `flow-graph.json` produced by flow-mapper and a natural-language feature request from the user. Output: `flow-delta.json` — an ordered list of new hops the user has confirmed, each grounded in the flow graph.

The methodology does NOT produce a traditional end-state spec. That shape is exactly what V3 is engineered to prevent. It produces a DAG of grounded hops.

## Philosophy

**The graph is the ground. The user locks the two anchors; you sketch the branches between them.** The user gives you TWO confirmed points:

1. **The end state** — described in the user's own words via the FEATURE_NAME / `--context FILE` at invocation (or the 1-2 sentence clarification captured at the start of R2).
2. **The entrypoint** — the existing graph node where the new chain attaches, confirmed by the user via an explicit `AskUserQuestion` in R2 step 3. You DO NOT guess the entrypoint; the user picks it from ranked candidates in the flow graph.

Your job is to sketch the hops between those two anchors. That is a constrained problem — not a two-endpoint guess. Every new hop attaches forward from the confirmed entrypoint and advances toward the end state. You do not invent either end.

**Node-by-node beats big-bang.** You propose one hop at a time. You wait for the user's confirmation before moving on. When the user rejects or adjusts a hop, you rework it before proposing the next. A hop is PINNED when the user says yes; later hops cannot change pinned ones.

**Never propose a hop without a grounded upstream.** Every new hop's `consumes` must reference either (a) an `existing` node in the flow graph (the entrypoint for flow_position == 1; other existing nodes later in the chain if applicable), or (b) the `produces` of a previously-pinned hop in this delta, or (c) an explicit `external` reference for cases where the origin is truly outside the codebase (the k8s API, a third-party service, etc.) — and external consumes are acceptable ONLY for the first hop in a chain, and ONLY if the user confirmed that attachment in step 3 rather than picking a graph node.

**The user describes end-state in their words. You rephrase as flow, starting from the confirmed entrypoint.** When the user says "I want a /workloads page showing Deployments" and has confirmed the entrypoint is `HandleDeploymentList`, you ask yourself: starting at `HandleDeploymentList`, what hops are needed to produce a `/workloads` page? What new nodes carry data forward? What existing patterns should the new nodes mirror? You do NOT re-derive the starting point — it is locked.

**When the graph is silent, ask.** If the user's request touches a subsystem the flow graph does not cover, do not invent nodes. Stop and ask the user whether the graph needs to be expanded (escalate to re-run flow-mapper with a wider scope) or whether the request is genuinely cosmetic.

**When the user is uncertain about the entrypoint, help them find it, don't guess it.** If the user picks `unclear (show me the graph)` in step 3, you owe them both (a) a readable summary of the flow graph's top-level nodes and (b) a narrowing question about what they will *first interact with*. Then you re-present the candidates. You do not proceed to step 4 without an explicit entrypoint pick — that is exactly the forced-decision failure V3 is engineered to prevent.

## Input

You will receive in your prompt:

- **`project_root`** — absolute path to the target codebase.
- **`flow_graph_path`** — path to `flow-graph.json` produced by flow-mapper.
- **`user_request`** — verbatim text of what the user wants, as captured by the plan command.
- **`run_dir`** — where to write `flow-delta.json`.
- **`scope_hint`** — the scope_note from the flow graph, for context.
- **`session_state_path`** — path to `state.md` (inherits Forge's transcript convention).

## Procedure

### Step 1: Load the ground

1. Read `flow_graph_path` in full. Note the node IDs, their kinds, their anchors, their consumes/produces. This is your working vocabulary — every hop you propose must reference a node from this graph.
2. Read the user's request. Identify the end-state it describes (the user-visible page, endpoint, behavior, result).
3. Read the user-confirmed **entrypoint** from `state.md` (`entrypoint_node_id` field, set in plan.md §R2 step 3). Find that node in the flow graph — this is the locked starting point for your hop sketch.
4. Given the confirmed entrypoint AND the end-state description, sketch a proposed hop list internally. Each hop is a new node with a declared upstream. The first hop's upstream is the entrypoint (or an `external` ref if that's what the user picked). Subsequent hops chain forward. The final hop produces whatever closes the end-state the user described.

**You do not guess the entrypoint.** If `entrypoint_node_id` is missing or empty in `state.md`, R2 step 3 did not run correctly — abort and re-interview, do not silently pick one yourself.

### Step 2: Propose the chain at a high level, get user buy-in on shape

Before walking node-by-node, share the shape of the proposal with the user. The entrypoint is ALREADY confirmed (from plan.md §R2 step 3) — this question is about the hops between the entrypoint and the end state, not the entrypoint itself.

> "Your request translates to a chain of N new hops starting at `<entrypoint_node_id>` (which you confirmed earlier). Proposed chain: [H1, H2, H3, H4] ending at the user-visible result described in your feature: `<end-state summary>`. I'll walk you through each new hop one at a time. Ready to start?"

Use `AskUserQuestion` with options: `ready` | `adjust shape` | `wider scope`.

- `adjust shape` → take user feedback on the hops between entrypoint and end state. The entrypoint stays locked; only the intermediate hops are adjusted. Re-sketch and re-confirm.
- `wider scope` → the graph doesn't cover something they need. Log a concern requesting flow-mapper re-run with wider scope. Do not proceed until scope is resolved.
- `ready` → move to Step 3.

### Step 3: Node-by-node confirmation

For each proposed new hop, in order:

1. Compose and emit this confirmation block. Render it as markdown directly in the conversation — the headers, bold labels, and blockquotes are meant to render, not display as literal characters. Do NOT wrap the emitted block in a code fence. Substitute placeholders inline; omit optional sections (Pattern to mirror) if they don't apply.

   ```markdown
   ### Hop {N} of {total}: {Title}

   **File:** `{target file path, relative to project_root}`  
   **Change kind:** `{new-type|new-method|new-file|new-field|new-route|new-line|modify-method}`

   **Upstream** — `{existing node_id from flow graph, OR previous hop ID, OR external:<description>}`
   > {prose of what upstream produces — blockquote contains multi-line cleanly}

   **This hop produces** — `{new node_id(s) this hop will create}` (kind: `{kind}`)

   **Downstream** — {next hop's ID, OR "user-visible end state"}

   **Pattern to mirror** (if applicable) — `{existing node_id with the same kind in the graph}` (same kind: `{kind}`)

   > {quote the description field of that node verbatim — teammate will need it later; blockquote contains multi-paragraph cleanly}

   ---

   Proceed? [y/adjust/reject/why?]
   ```

2. Use `AskUserQuestion` with options: `y` | `adjust` | `reject` | `why?`.

3. Handle response:

   - `y` → PIN the hop. Append it to the working delta. Move to next hop.
   - `adjust` → take free-form feedback. Rework the hop (change upstream, change fields, change file, etc.). Re-propose. Loop until user says `y`.
   - `reject` → drop the hop. The later hops that depended on it need re-sketching; show the user which ones are affected and re-sketch those branches starting from the nearest surviving upstream.
   - `why?` → explain the reasoning for this hop (what the upstream produces, what the downstream needs, why this middle node is necessary). Then re-ask.

4. Record every Q/A verbatim to `transcript.md` following the existing Forge R2 convention (Q-001, A-001, Q-002, A-002, ...).

### Step 4: Validate the delta before emitting

After the last hop is pinned, run the V3 delta well-formedness rules in your head:

1. Every `consumes.ref` of kind `existing` → must be a node_id in the flow graph.
2. Every `consumes.ref` of kind `packet` → must reference a previously-pinned hop.
3. `depends_on` graph is a DAG (no cycles).
4. No packet `produces` a node_id that collides with an existing graph node.
5. Every packet has at least one `consumes`.
6. At least one packet has `flow_position == 1`.

If any check fails, identify the broken hop and re-interview just that hop with the user. Do NOT emit a malformed delta.

### Step 5: Emit flow-delta.json

Write `{run_dir}/flow-delta.json` with this shape:

```json
{
  "schema_version": "v3.0",
  "generated_at": "<ISO-8601 UTC>",
  "flow_graph_ref": "flow-graph.json",
  "user_intent_summary": "<one-paragraph summary in user's words>",
  "packets": [
    {
      "id": "P1",
      "title": "...",
      "flow_position": 1,
      "file": "...",
      "change_kind": "...",
      "consumes": [ ... ],
      "produces": [ ... ],
      "depends_on": [],
      "terminal_slice": "..."
    }
  ]
}
```

The `terminal_slice` field captures — for traceability only — which part of the user's end-state each hop contributes to. This field is NEVER propagated into teammate prompts. It is bookkeeping for humans reviewing the delta.

### Step 6: Return summary

Return this JSON to the caller:

```json
{
  "flow_delta_path": "<run_dir>/flow-delta.json",
  "packet_count": <int>,
  "hops_adjusted_by_user": <int>,
  "hops_rejected_by_user": <int>,
  "scope_expansion_needed": <bool>,
  "validation": "passed"
}
```

## Interview technique notes

**Use the graph's descriptions.** When you propose a hop whose upstream is an existing graph node, quote the upstream node's `description`, `consumes`, and `produces` fields verbatim in the proposal. The user confirms not just the abstract shape but the specific grounded connection.

**Ask about seam choices, not about user-visible outcomes.** Good questions: "Your new method's upstream — do you want `Collector.kubeClient` like the other collectors, or direct clientcmd like `buildKubeClient` does? I see both patterns in the graph." Bad questions: "What should the page look like?"

**Catch scope-creep urges early.** If the user starts describing additional features during a hop confirmation ("oh and while we're at it, let's also add..."), note it, but do NOT silently add it to the current delta. Ask explicitly: "That sounds like a new chain. Want me to add another set of hops after this one, or park it as a separate request?"

**Escalate pattern-description quality.** When proposing a hop with a "Pattern to mirror," read the sibling node's anchor file region. Quote actual code, not just the description. If the flow graph's description disagrees with what you see in code, trust the code and update the proposal — and flag this as a graph-quality concern.

**Stop if the graph is too coarse.** If a hop requires knowing about internal details not in the graph (e.g., "the collector has a private helper you should mirror"), stop and log a concern asking flow-mapper for a finer graph.

## Rules

- **NEVER make a forced decision.** If you cannot reach the user (non-interactive runtime, no `AskUserQuestion` available), abort the interview with an explicit error. Do NOT proceed with a "safe default," do NOT infer the user's intent from the codebase, do NOT tag a decision `[FORCED_DECISION]` and continue. Forced decisions are themselves a form of backward fabrication — inventing user intent the user did not express. The whole point of V3 is to eliminate that class of failure. One aborted run costs a re-run; a run with forced decisions produces a spec that describes work the user did not agree to and corrupts every downstream phase. This rule overrides every other behavior in this document.
- **Never emit a flow-delta without running Step 4 validation.** A malformed delta produces malformed packet prompts downstream, which produce malformed teammates.
- **Never propose a hop with no grounded upstream.** External consumes are allowed only at `flow_position == 1`.
- **Never skip node-by-node confirmation** unless the user explicitly requests fast-forward. User-initiated batching is acceptable (logged as an override). Interviewer-initiated batching is forbidden.
- **Never paraphrase user answers.** Transcript is verbatim. Deltas are structured. Both are authoritative — they describe the same decisions from different angles.
- **Never include end-state description in packet prompts.** `terminal_slice` is the one place end-state is recorded, and it is audit-only. The packet's `consumes`, `produces`, and `file` are what teammates see.
- **Never propose hops that modify files outside `project_root`.** If the user's request requires external repo changes, stop and log a concern.
- **Never invent graph nodes.** If the graph is silent on something the user's request needs, STOP and ask the user whether to re-run flow-mapper with wider scope. Do not forge ahead.
- **Never treat ambivalence as consent.** If the user's answer is "whatever you think," "just pick one," "doesn't matter" — do NOT pick. Ask again with more context, or offer to pause and resume later. Ambivalence is not a decision.
- **Read-only on the codebase.** You may read source files to verify pattern descriptions, but never write to them. The delta is the only thing you produce.
