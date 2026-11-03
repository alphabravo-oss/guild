---
description: "Build a spec with trowel: one builder, a code check and a spec-to-code check, capped fix rounds"
argument-hint: "<spec> [--max-rounds N] [--max-agents N] [--resume RUN]"
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/trowel:*)", "Read", "Workflow"]
disable-model-invocation: "true"
---

# trowel build

```!
"${CLAUDE_PLUGIN_ROOT}/scripts/trowel" --root "${CLAUDE_PROJECT_DIR:-$PWD}" args $ARGUMENTS
```

You launch trowel's build as the `trowel-build` workflow (`${CLAUDE_PLUGIN_ROOT}/workflows/build.js`) and report what
it returns. The script holds the whole flow; you do not build, verify or run trowel commands yourself.

## 1. Read the parsed arguments

The command above printed one JSON line. If `ok` is false, show `stop` and end here.

## 2. Launch

Call the **Workflow** tool with:

- `scriptPath`: `<args.plugin_root>/workflows/build.js`
- `args`: the `args` object from that line, passed as a JSON object, not a string.

Then say one line and wait; do not poll:

```
trowel is building <spec> as run <args.run> (workflow <runId>). A run that stops part way continues with /trowel:build --resume <args.run>.
```

## 3. When it returns

The workflow returns `{status, message, run, run_dir, report, counts, mode, rounds, agent_calls, concerns, timeline}`.

- `done` or `done_with_gaps`: show `message`, then read `report` and show its summary lines and every requirement that
  is not MET, with the reason the report gives, and every remaining failure. Show `concerns`. Say plainly what is not
  met first.
- `capped`: the run reached its agent-call cap (`--max-agents`) and stopped early; the report covers what was done.
  Show it as for `done_with_gaps`.
- `blocked`: preflight or planning stopped the run before building. Show `message` verbatim: it names the command that
  failed and its output, or the rule a plan broke. Nothing was built.
- `stopped`: `init` or `resume` refused (a dirty tree, a broken seal, an existing run, the wrong branch). Show `message`.
- `error`: show `message` and the last lines of `timeline`. A rerun with `--resume <run>` continues from the last
  completed step.

Do not retry on your own.
