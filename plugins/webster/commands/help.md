---
description: What webster does and the order to run it in
---

Tell the user, briefly:

```
/webster:plan     survey the stack, name the subjects, scaffold the tree and the site
/webster:write    extract reference material, write the pages, record the drift manifest
/webster:audit    drift, layout, six gates, prioritised punch list. read-only
```

Run them in that order the first time. `audit` works standalone on documentation the plugin
never wrote.

The three scripts are runnable on their own, which is what makes them usable in CI:

```
python3 scripts/survey.py .        stack, frameworks, and the public surface with anchors
python3 scripts/scaffold.py check  the layout gate. exit 1 on any violation
python3 scripts/drift.py check     which pages a diff invalidated. exit 1 on drift
python3 scripts/llmstxt.py         an llms.txt built from pages that exist on disk
```

It replaces four plugins: `documentation-generation`, `code-documentation` (a strict subset of
the first), `documentation-standards` and `repo-doctor`. What is new is that reference material
is extracted rather than composed, every claim carries a source or a `[?]` tag, drift is measured
against a recorded manifest rather than re-read, and a gate that could not run reports
`not_checked` instead of a pass.
