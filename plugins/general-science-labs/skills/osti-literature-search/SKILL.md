---
name: osti-literature-search
description: |
  Cross-domain / Scientific literature retrieval — Search and retrieve
  DOE-funded scientific literature from OSTI.GOV using `osti-axi`, a
  third-party read-only CLI. NOTE: this skill is a POINTER — the tool is NOT
  bundled with this catalog and must be installed separately from its own
  repository, with the user's explicit consent. Use for finding papers,
  reports, and datasets funded by the U.S. Department of Energy, pulling a
  record's abstract and resource links, and downloading OSTI-provided PDF/text
  full text into the workspace. WHEN: search OSTI, OSTI.GOV, DOE research
  papers, national laboratory publications, find DOE-funded literature,
  scientific literature search, download full text paper, get paper PDF,
  osti-axi, OSTI record, prior art for a DOE proposal, literature review for a
  national lab project.
metadata:
  version: "1"
  category: "Cross-domain"
  subfield: "Scientific literature retrieval"
---

# osti-literature-search — OSTI.GOV literature retrieval (external tool)

> [!IMPORTANT]
> **This skill does not contain the tool.** It is a pointer to
> [`davenovelli-pnnl/osti-axi`](https://github.com/davenovelli-pnnl/osti-axi),
> a third-party MIT-licensed CLI by Dave Novelli (Pacific Northwest National
> Laboratory). No upstream source code is vendored in this catalog. Using this
> skill means installing and executing software from another repository.
> **Get the user's explicit consent before installing anything.**

Canonical metadata for this tool — pinned commit, prerequisites, verified
install steps — lives in [`tools/external-tools.json`](../../tools/external-tools.json)
at the root of this catalog. That file is the source of truth; this skill
follows it.

## What the tool does

`osti-axi` queries [OSTI.GOV](https://www.osti.gov), the U.S. Department of
Energy's public repository of DOE-funded research output. It is built as an
**AXI** (Agent eXperience Interface): output is [TOON](https://toonformat.dev)
on stdout rather than prose, and each response ends with an `[AXI-NEXT]` block
suggesting follow-up commands — so it is designed to be driven by an agent, not
read by a human.

It is **read-only** against OSTI. It never publishes, edits, submits, or manages
accounts, and it never extracts text from PDFs — PDF and text downloads are
separate representations that OSTI itself supplies.

## When to use it

- "Find DOE papers on <topic>"
- "Search OSTI for <author / national lab / subject>"
- "What's the prior art for this proposal?"
- "Get me the full text of OSTI record <id>"
- Literature review or citation gathering for a national-lab project

If the user wants general academic literature (arXiv, PubMed, Semantic
Scholar), this is the wrong tool — OSTI covers DOE-funded output specifically.

## Step 1 — check whether it's already installed

Always check before proposing an install:

```bash
osti-axi --version    # expect: 0.1.0
```

If that prints a version, skip to **Usage**.

## Step 2 — install (ONLY with explicit user consent)

> [!WARNING]
> This installs and globally executes third-party code from a repository with
> no releases, no npm package, and no independent review. `npm install -g`
> places a binary on the user's `PATH`.
>
> **Ask the user first. Do not install autonomously.** Show them the repository
> URL and the pinned commit, and let them decline.

Upstream has no tags or releases, so pin to the reviewed commit:

```bash
git clone https://github.com/davenovelli-pnnl/osti-axi.git
cd osti-axi
git checkout 167b641cffeedfc8b3df9154f999fa951f950649
npm install        # devDependencies, incl. TypeScript
npm run build      # compiles dist/ — REQUIRED
npm install -g .
osti-axi --version # expect: 0.1.0
```

Requires **Node >= 20**.

### Install pitfalls (verified, not theoretical)

Two shorter install routes look correct and silently fail:

- **`npm install -g git+https://github.com/davenovelli-pnnl/osti-axi.git#<sha>`
  does not work.** The package declares a `prepack` script but no `prepare`
  script, so npm never builds `dist/` for git-spec installs, and the declared
  bin target `./dist/bin/osti-axi.js` does not exist.
- **Clone followed directly by `npm install -g .` does not work either.**
  Without a prior `npm install` and `npm run build`, the TypeScript is never
  compiled and no binary is produced.

The sequence above is the one that was actually installed and smoke-tested.
If a install appears to succeed but `osti-axi` is not found, a missing build
step is almost certainly why.

## Usage

Three commands. Run `osti-axi <command> --help` for the complete, authoritative
flag reference — prefer that over anything memorized, since this skill
deliberately summarizes rather than duplicates upstream documentation.

```bash
# Search — positional query optional when filters are supplied
osti-axi search "quantum sensing" --fulltext-only
osti-axi search --research-org PNNL --sort publication_date --order desc
osti-axi search --author "Chen, Kun" --publication-from 01/01/2020

# Digest — one record, abstract + resource links
osti-axi digest 3376511 --full

# Download — OSTI-provided PDF/text into ./osti-downloads/
osti-axi download 2569708 3376511 --format both
```

Filter families available on `search`: content (`--title`, `--author`,
`--subject`, `--fulltext`), identity (`--osti-id`, `--doi`), organization
(`--sponsor-org`, `--research-org`), scope (`--language`, `--country`,
`--has-fulltext`), date ranges (`--publication-from/to`, `--entry-from/to`),
and result shaping (`--sort`, `--order`, `--limit`, `--page`, `--fields`).
Dates use `MM/DD/YYYY`.

## Working with the output

- Output is **TOON**, not JSON and not prose. Parse it; don't hand it to the
  user raw unless they asked for it.
- Results default to `id,title,date,has_fulltext`. Use `--fields` to pull
  `authors`, `doi`, `abstract`, `subjects`, `fulltext_url`, and others.
- Exit codes: `0` success, `1` error, `2` usage error.
- Read the trailing `[AXI-NEXT]` block — it suggests the next command, which
  usually saves a round trip.
- `download` writes into the **current working directory** under
  `./osti-downloads/` by default. Confirm the destination with the user before
  downloading in bulk, and use `--out` to redirect.

## Good practice

- Search before downloading. Use `--fulltext-only` to avoid records with no
  retrievable text.
- Narrow with `--limit` first; OSTI result sets are frequently in the tens of
  thousands.
- Cite by OSTI ID and DOI when summarizing findings, so claims stay traceable.
- Don't bulk-download unless the user asked — these are real PDFs landing in
  their workspace.

## Attribution

`osti-axi` is © its authors and licensed MIT. This catalog neither vendors nor
modifies it, and makes no warranty about it. File bugs and feature requests
upstream at <https://github.com/davenovelli-pnnl/osti-axi>, not against this
catalog. If the pinned commit here has fallen behind, that is an issue for
*this* catalog.
