---
description: What webster does and the order to run it in
---

Tell the user, briefly:

```
/webster:plan     see the product the way a person does, name the readers and the subjects,
                  scaffold the tree and the site
/webster:write    extract reference material, write the pages, record the drift manifest
/webster:audit    drift, layout, seven gates, prioritised punch list. read-only
```

Run them in that order the first time. `audit` works standalone on documentation the plugin
never wrote.

The three scripts are runnable on their own, which is what makes them usable in CI:

```
python3 scripts/survey.py .        two surfaces: the code's, and the one a person sees
                                   (user_surface: screens, labels, error messages, commands)
python3 scripts/scaffold.py check  the layout gate. exit 1 on any violation
python3 scripts/doctype.py types   the content types, the three readers, what each may name
python3 scripts/doctype.py check   type, reader, lens and jargon. exit 1 on a defect
python3 scripts/drift.py check     which pages a diff invalidated. exit 1 on drift
python3 scripts/rendered.py check  what reached the reader in the built HTML. exit 1 on a leak
python3 scripts/llmstxt.py         an llms.txt built from pages that exist on disk
```

**The rule that shapes everything else**: every page declares who it is for, and that decides
what the page may be about. A page for a `user` names screens, buttons, fields and what the
product gives back. It does not name a symbol, a request route, an environment variable or a part
of the architecture, because the reader cannot act on any of those. Those pages exist; they live
in `developer/` and `advanced/` and they declare a different reader. `WEBSTER_LENS_ALLOW` widens
the vocabulary where a product's readers genuinely use it.

It replaces four plugins: `documentation-generation`, `code-documentation` (a strict subset of
the first), `documentation-standards` and `repo-doctor`. What is new is that the product is read
before the source is, reference material is extracted rather than composed, every claim carries a
source or a `[?]` tag, drift is measured against a recorded manifest rather than re-read, and a
gate that could not run reports `not_checked` instead of a pass.
