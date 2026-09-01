---
name: structure
description: The mandatory documentation layout, which is the one Harvester uses. Subject-first directories, a Docusaurus site whose navigation is the filesystem, and the frontmatter and naming rules that go with it. Load before planning or writing any page.
---

# Structure

**Every set of documentation this plugin produces uses this layout.** It is not chosen per repo
and it is not negotiable, because a reader who learns where things live in one product should
find them in the same place in the next.

The model is [Harvester](https://github.com/harvester/docs): Docusaurus, a sidebar generated from
the filesystem, and subject-first directories.

## The tree

```
docs/
├── index.md                    slug: /, the one-sentence answer to "what is this"
├── faq.md
├── getting-started/            position 2
│   ├── overview.md
│   ├── glossary.md             every term a newcomer will not know
│   └── document-conventions.md how to read the rest
├── install/                    position 3, one page per target
├── <subject>/                  positions 4 upward, one per thing in the product
│   ├── _category_.json
│   ├── <anything>.md           the landing page, marked sidebar_position: 1
│   └── <verb>-<noun>.md        one task per page
├── api/                        position 90, only when there is a spec to generate from
├── advanced/                   position 91
├── troubleshooting/            position 92
└── developer/                  position 93
```

Plus up to four cross-cutting pages at the root beyond `index.md` and `faq.md`, for a topic that
genuinely spans every subject. Harvester has two, `airgap.md` and `authentication.md`. More than
four means a subject was never named.

Scaffold it rather than creating it by hand:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py init \
  --title "Name" --description "one sentence" \
  --subject "vm:VM Management,volume:Volumes" --site --api \
  --url https://docs.example.com --org owner --project repo \
  --edit-url https://github.com/owner/repo/edit/main/
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py check
```

Pass `--api` only when `survey.py` found an OpenAPI spec. Harvester's `docs/api` is generated at
build time from its swagger files by `docusaurus-plugin-openapi-docs`, so an empty `api/` on a
product with no spec is a section nobody can fill.

**These rules were checked against Harvester, not assumed.** Running the checker over
`harvester/docs` returns one finding, and it is a real ordering bug in their tree rather than a
disagreement about the layout.

`check` exits 1 on any violation and is the Shaped gate.

## Each section has a reader

The tree serves three of them, and that is the point of splitting it. `house-rules` section 6
carries the full table; the short version is that `getting-started/` and `troubleshooting/` are
for the person using the product, `install/` and `advanced/` are for whoever runs it, and `api/`
and `developer/` are for whoever builds against it.

`scaffold.py init` writes the right `audience:` into every page it creates, and
`scaffold.py check` reports a page whose declared audience disagrees with the section it sits
in. Pass `--subject-audience` when the subject directories serve operators or developers rather
than users, which is the case for a product that is itself infrastructure.

## Subject-first, and why

Harvester puts `vm/`, `volume/`, `networking/` at the top level, not `tutorials/` and
`how-to/`. A reader arrives knowing what they want to work on, not which genre of document they
need. Sorting by genre asks them to classify their own question before they can navigate.

**Diataxis still applies, one level down.** Inside `vm/` you write the overview as explanation,
`create-vm.md` as a tutorial, `hotplug-volume.md` as a how-to, and you keep reference in `api/`.
The four modes govern how a page is written. They never govern where it lives.

## Choosing subjects

A subject is a thing in the product a user would say out loud. Derive them from the surface that
`scripts/survey.py` found, and from the product's own vocabulary.

- **Good:** `vm`, `volume`, `networking`, `checks`, `scanning`, `billing`, `webhooks`
- **Bad:** `utils`, `core`, `misc`, `guides`, `reference`, `advanced-topics`, anything named
  after a code module rather than a user's concern

Three to twelve subjects. One is not a structure. More than twelve means two of them are the
same thing.

**A small product still gets the full tree.** `install/` with one page and an empty `developer/`
are correct: the shape is stable so it can be grown into, and an empty section is a visible,
honest gap rather than a decision nobody recorded. Do not delete sections to tidy up.

## Naming

- Directories and files are `lower-case-with-hyphens`. No underscores, no capitals.
- **The landing page is the one carrying `sidebar_position: 1`, and its filename is free.**
  Harvester uses `host/host.md`, `vm/virtual-machines.md`, `logging/harvester-logging.md`,
  `image/upload-image.md` and `volume/create-volume.md`. Nine of its fifteen directories do not
  name the landing page after the directory, so a naming rule would be inventing a convention
  Harvester does not follow. What is invariant is that every directory has exactly one page at
  position 1. Two pages claiming position 1 is a violation, because Docusaurus then breaks the
  tie alphabetically and the sidebar stops matching the numbers.
- Task pages are named for the task: `create-vm.md`, `hotplug-volume.md`, `restore-a-backup.md`.
  Not `vm-2.md`, not `advanced-vm.md`.

## Frontmatter

Every page. The scaffold writes it, so the job is to keep it accurate.

```yaml
---
sidebar_position: 3
sidebar_label: Create a VM      # only when it differs from the title
title: "Create a virtual machine"
description: One sentence, used by search and by link previews.
keywords:
  - vm
---
```

`_category_.json` per directory, and no two directories share a `position`. Harvester itself
gets this wrong: `image` and `networking` are both at 10, so their real order is alphabetical
rather than intended. The checker catches it.

```json
{ "position": 8, "label": "VM Management", "collapsible": true, "collapsed": true }
```

## The site

`scaffold.py init --site` writes the site into its own directory, `website/` by default and
`--site-dir` to change it. That separation is not tidiness: these files at a repo root collide
with whatever application is already there, and on a Next.js repo a `src/css/custom.css` lands
inside the application source.

It writes `docusaurus.config.js`, `sidebars.js`, `package.json`, a `.gitignore` and a stub
`src/css/custom.css`, in Harvester's configuration: the classic preset, `routeBasePath: '/'`, an
autogenerated sidebar, `showLastUpdateTime`, and edit links back to the repo. The `docs` path is
resolved back out of the site directory, so the pages stay where they are.

```bash
cd website
npm install && npm start          # local, on port 3000
npm run build                     # static output in website/build
npm run version -- v1.0           # freeze the current docs as a version
```

If the repo gitignores its docs directory, the site cannot be published from CI until that
changes. Say so rather than working around it.

Two things the scaffold deliberately leaves to you. **The palette**, because
`src/css/custom.css` is a stub and the Docusaurus default is a default; `no-slop` covers what to
avoid. And **versioning**, because cutting a version is a release decision, not a docs decision.

The published site is a UI. Judge it as one with a dedicated UI review tool run against the
built site, and if none is available say so and mark it `not_checked` rather than eyeballing it.
