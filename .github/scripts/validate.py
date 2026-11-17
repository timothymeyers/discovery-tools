#!/usr/bin/env python3
"""Validate the discovery-tools catalog.

Checks the Agent Skills spec, plugin/marketplace manifest integrity, and the
repo-specific safety rules that keep sensitive data and hardcoded install paths
out of published skills.

Usage: python3 .github/scripts/validate.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Agent Skills spec allow-list. The spec's reference validator (skills-ref) and
# Anthropic's package_skill.py both treat any other top-level key as a HARD ERROR
# rather than ignoring it, so a stray `version:` breaks publishing and upload.
# Versions belong under `metadata:` (a string->string map for non-spec fields).
ALLOWED_FRONTMATTER = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}

# Paths that must never be baked into a published skill.
HARDCODED_PATH_RE = re.compile(
    r"~/\.copilot/skills/[a-z]|~/\.agents/skills/[a-z]|/Users/[a-z]|/home/[a-z]"
)

# Generated artifacts that must never be committed.
FORBIDDEN_FILES = ("token_usage_mined.json",)

SECRET_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)

TEXT_EXT = (".md", ".py", ".sh", ".json", ".txt", ".yml", ".yaml")

# Directories that are build/test output and must never be committed.
FORBIDDEN_DIRS = (".pytest_cache", "__pycache__", ".ruff_cache", ".mypy_cache")

# Controlled vocabulary for metadata.category. Deliberately broader than the
# catalog currently needs: a vocabulary that only covers today's skills forces
# validator and docs churn the first time someone adds a new domain. Extend this
# list (and the README/CONTRIBUTING tables) rather than inventing values ad hoc.
CATEGORIES = {
    "Life sciences",
    "Physical sciences",
    "Earth and environmental sciences",
    "Engineering and materials",
    "Computer and information sciences",
    "Mathematics and statistics",
    "Cross-domain",
}

# The visible half of the two-layer taxonomy. Only `name` and `description` are
# loaded at agent startup, so a taxonomy that lives only in `metadata:` is inert
# for routing. Every description must therefore lead with "Category / Subfield — ".
# Matched against whitespace-normalised text, because the cue routinely spans a
# line wrap and category names contain hyphens ("Cross-domain").
CUE_RE = re.compile(r"^[A-Z][A-Za-z&\- ]+ / .+? — \S")


def normalize(text):
    """Collapse all whitespace, so line wrapping never changes a match."""
    return " ".join((text or "").split())

errors = []
warnings = []

# skill name -> metadata.category, populated by check_skill()
SKILL_CATEGORIES = {}

# skill name -> repo-relative skill directory, populated by discover_skills()
SKILL_DIRS = {}


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def parse_frontmatter(text, path):
    """Minimal YAML frontmatter reader.

    Returns (fields, metadata) where `fields` maps top-level keys to their
    de-indented scalar/block text, and `metadata` maps the nested keys under
    `metadata:` to their values. Deliberately avoids a PyYAML dependency so the
    validator runs anywhere with bare Python 3.
    """
    if not text.startswith("---"):
        # A leading BOM or zero-width space makes frontmatter silently fail to
        # parse while still *looking* correct in an editor. Call it out by name.
        stripped = text.lstrip("\ufeff\u200b\u200c\u200d\u2060")
        if stripped.startswith("---"):
            bad = text[: len(text) - len(stripped)]
            err(
                f"{path}: file begins with invisible character(s) "
                f"{[hex(ord(c)) for c in bad]} before the '---' frontmatter "
                f"delimiter, which breaks YAML parsing. Strip them."
            )
        else:
            err(f"{path}: missing YAML frontmatter")
        return None, None
    end = text.find("\n---", 3)
    if end == -1:
        err(f"{path}: unterminated frontmatter")
        return None, None

    fields, raw, key, buf = {}, {}, None, []
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            if key:
                fields[key] = "\n".join(x.strip() for x in buf).strip()
                raw[key] = buf
            key = m.group(1)
            val = m.group(2).strip()
            buf = [] if val in ("|", ">", "|-", ">-", "") else [val]
        elif key:
            buf.append(line)
    if key:
        fields[key] = "\n".join(x.strip() for x in buf).strip()
        raw[key] = buf

    # `metadata:` must be a nested map; parse its indented child keys.
    metadata = None
    if "metadata" in fields:
        metadata = {}
        for line in raw.get("metadata", []):
            if not line.strip():
                continue
            if not line.startswith((" ", "\t")):
                metadata = None  # not an indented block -> not a map
                break
            mm = re.match(r"^\s+([A-Za-z0-9_.-]+):[ \t]*(.*)$", line)
            if mm:
                metadata[mm.group(1)] = mm.group(2).strip().strip('"\'')
    return fields, metadata


def check_skill(skill_dir):
    name = os.path.basename(skill_dir)
    rel = os.path.relpath(skill_dir, ROOT)
    skill_md = os.path.join(skill_dir, "SKILL.md")

    if not os.path.isfile(skill_md):
        err(f"{rel}: no SKILL.md")
        return

    text = open(skill_md, encoding="utf-8").read()

    # Progressive disclosure: name+description load at startup, the body loads on
    # trigger. An oversized body is a real context cost, so warn (the spec states
    # this as guidance, not a hard limit — don't fail a working skill over it).
    nlines = text.count("\n") + 1
    if nlines > 500:
        warn(
            f"{rel}/SKILL.md is {nlines} lines (~{len(text) // 1024}KB); guidance is "
            f"under ~500. Consider moving detail into references/ for progressive "
            f"disclosure."
        )

    fm, metadata = parse_frontmatter(text, f"{rel}/SKILL.md")
    if fm is None:
        return

    # --- Agent Skills spec ---
    declared = fm.get("name")
    if not declared:
        err(f"{rel}: frontmatter missing required field 'name'")
    elif declared != name:
        err(f"{rel}: frontmatter name {declared!r} != directory name {name!r}")
    elif not NAME_RE.match(declared) or len(declared) > 64:
        err(f"{rel}: name {declared!r} is not lowercase-hyphenated, 1-64 chars")

    desc = fm.get("description")
    if not desc:
        err(f"{rel}: frontmatter missing required field 'description'")
    elif len(desc) > 1024:
        err(f"{rel}: description is {len(desc)} chars (limit 1024)")
    elif not CUE_RE.match(normalize(desc)):
        err(
            f"{rel}: description must lead with a domain cue in the form "
            f"'Category / Subfield — ...'. Only name and description load at agent "
            f"startup, so a taxonomy kept solely in 'metadata:' cannot influence "
            f"routing. Got: {desc[:60]!r}"
        )

    if "allowed-tools" in fm and fm["allowed-tools"].lstrip().startswith(("[", "-")):
        err(f"{rel}: 'allowed-tools' must be a space-separated string, not an array")

    extra = set(fm) - ALLOWED_FRONTMATTER
    if extra:
        err(
            f"{rel}: unexpected top-level frontmatter field(s) "
            f"{', '.join(sorted(extra))}. The Agent Skills spec allows only "
            f"{', '.join(sorted(ALLOWED_FRONTMATTER))} — anything else is a hard "
            f"error for skills-ref validate and Anthropic packaging. "
            f"Put a version under 'metadata:' instead."
        )

    if fm.get("metadata") is not None and metadata is None:
        err(f"{rel}: 'metadata' must be a map of string keys to string values")
    elif metadata is not None:
        for k, v in metadata.items():
            if not v:
                err(f"{rel}: metadata.{k} has no value; must be a string")
        if "version" in metadata and not re.fullmatch(r"[\w.+-]+", metadata["version"]):
            err(f"{rel}: metadata.version {metadata['version']!r} is not a plain string")

    # --- domain taxonomy (portable half) ---
    meta = metadata or {}
    category, subfield = meta.get("category"), meta.get("subfield")
    if not category:
        err(
            f"{rel}: metadata.category is required so installers and agents can tell "
            f"which scientific domain this skill serves. One of: "
            f"{', '.join(sorted(CATEGORIES))}"
        )
    elif category not in CATEGORIES:
        err(
            f"{rel}: metadata.category {category!r} is not in the controlled "
            f"vocabulary. Use one of {', '.join(sorted(CATEGORIES))}, or extend "
            f"CATEGORIES here and in README.md/CONTRIBUTING.md together."
        )
    if not subfield:
        err(f"{rel}: metadata.subfield is required (free text, e.g. 'Enzymology')")
    SKILL_CATEGORIES[name] = category

    # The two halves must agree, or the visible cue misleads about the portable
    # taxonomy and the README table built from metadata will not match it.
    if category and subfield and desc and CUE_RE.match(normalize(desc)):
        cue = normalize(desc).split(" — ", 1)[0].strip()
        cue_category = cue.split(" / ", 1)[0].strip()
        if cue_category != category:
            err(
                f"{rel}: description cue category {cue_category!r} does not match "
                f"metadata.category {category!r}"
            )

    if os.path.isdir(os.path.join(skill_dir, "reference")):
        err(f"{rel}: use 'references/' (plural), not 'reference/'")

    # --- repo safety rules ---
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        # Build/test caches are generated artifacts; never publish them.
        for bad_dir in list(dirnames):
            if bad_dir in FORBIDDEN_DIRS:
                err(
                    f"{os.path.relpath(os.path.join(dirpath, bad_dir), ROOT)}: "
                    f"generated cache directory must not be committed"
                )
                dirnames.remove(bad_dir)

        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            frel = os.path.relpath(fp, ROOT)

            if fn in FORBIDDEN_FILES:
                err(f"{frel}: generated artifact must not be committed")
                continue
            if not fn.endswith(TEXT_EXT):
                continue

            body = open(fp, encoding="utf-8", errors="replace").read()

            if SECRET_RE.search(body):
                err(f"{frel}: looks like it contains a credential")

            for i, line in enumerate(body.splitlines(), 1):
                if HARDCODED_PATH_RE.search(line):
                    err(
                        f"{frel}:{i}: hardcoded install/home path — skills must resolve "
                        "their payload at runtime"
                    )

            if fn.endswith(".py") and 'SCRIPT_DIR, "token_usage_mined.json"' in body:
                err(f"{frel}: script writes generated output into its own directory")

            if fn.endswith(".json"):
                try:
                    json.loads(body)
                except json.JSONDecodeError as e:
                    err(f"{frel}: invalid JSON — {e}")


def check_manifests():
    gh_mkt = os.path.join(ROOT, ".github", "plugin", "marketplace.json")
    cc_mkt = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
    plugin = os.path.join(ROOT, "plugin.json")

    for p in (gh_mkt, cc_mkt, plugin):
        if not os.path.isfile(p):
            err(f"{os.path.relpath(p, ROOT)}: missing")
            return

    try:
        a = json.load(open(gh_mkt, encoding="utf-8"))
        b = json.load(open(cc_mkt, encoding="utf-8"))
        pj = json.load(open(plugin, encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(f"manifest is not valid JSON — {e}")
        return

    # The docs promise these two are byte-identical (a real-file copy, never a
    # symlink, because symlinks degrade on Windows clones and ZIP exports).
    # Comparing parsed JSON would let formatting drift through silently.
    if open(gh_mkt, "rb").read() != open(cc_mkt, "rb").read():
        err(
            ".github/plugin/marketplace.json and .claude-plugin/marketplace.json are "
            "not byte-identical; run: "
            "cp .github/plugin/marketplace.json .claude-plugin/marketplace.json"
        )

    for field in ("name", "plugins"):
        if field not in a:
            err(f"marketplace.json: missing required field {field!r}")
    if "name" not in pj:
        err("plugin.json: missing required field 'name'")

    versions = {pj.get("version"), a.get("metadata", {}).get("version")}
    versions |= {p.get("version") for p in a.get("plugins", [])}
    versions.discard(None)
    if len(versions) > 1:
        # A released catalog that reports three different versions is a real
        # publishing defect, not a style nit.
        err(f"version fields are out of sync across manifests: {sorted(versions)}")

    # The root plugin is the "everything" plugin: it must list every plugin's
    # skills/ directory, and nothing else, or it silently drops a plugin.
    declared = pj.get("skills", [])
    if isinstance(declared, str):
        declared = [declared]
    for s in declared:
        if not os.path.isdir(os.path.join(ROOT, s.rstrip("/"))):
            err(f"plugin.json: declared skills path {s!r} does not exist")
    expected = {f"plugins/{p}/skills" for p in plugin_dirs()}
    got = {os.path.normpath(s) for s in declared}
    if got != expected:
        missing, extra = sorted(expected - got), sorted(got - expected)
        err(
            f"plugin.json: 'skills' must list exactly every plugins/<name>/skills/ "
            f"directory so the discovery-tools plugin installs everything. "
            f"Missing: {missing or '-'}; unexpected: {extra or '-'}"
        )

    if "agents" in pj:
        ad = os.path.join(ROOT, pj["agents"].rstrip("/"))
        has_agents = os.path.isdir(ad) and any(
            f.endswith(".agent.md") for f in os.listdir(ad)
        )
        if not has_agents:
            err(f"plugin.json: declares agents at {pj['agents']!r} but none were found")

    listed = set()
    for entry in a.get("plugins", []):
        if check_plugin_entry(entry, versions):
            listed.add(entry.get("name"))
    for p in plugin_dirs():
        if p not in listed:
            err(f"plugins/{p}: has no entry in .github/plugin/marketplace.json")

    print("manifests OK")


def check_plugin_entry(entry, versions):
    """Lint a marketplace entry that points at a plugin under plugins/.

    Every skill lives in exactly one plugins/<name>/skills/ directory, as a real
    directory. Plugins cannot depend on each other, so the root plugin.json is
    the "everything" plugin and lists all of them. Returns True when the entry
    is a local plugins/ entry.
    """
    name = entry.get("name", "<unnamed>")
    source = entry.get("source")
    if not isinstance(source, str) or source.rstrip("/") in (".", ""):
        return False
    rel = os.path.normpath(source)
    pdir = os.path.join(ROOT, rel)
    if not os.path.isdir(pdir):
        err(f"marketplace.json: plugin {name!r} source {source!r} does not exist")
        return False
    if rel != os.path.join("plugins", name):
        err(f"marketplace.json: plugin {name!r} source must be ./plugins/{name}, got {source!r}")

    manifest = os.path.join(pdir, "plugin.json")
    if not os.path.isfile(manifest):
        err(f"{rel}: missing plugin.json")
        return True
    try:
        pj = json.load(open(manifest, encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(f"{rel}/plugin.json: invalid JSON — {e}")
        return True
    if pj.get("name") != name:
        err(f"{rel}/plugin.json: name {pj.get('name')!r} does not match marketplace entry {name!r}")
    if pj.get("version") and pj.get("version") not in versions:
        err(
            f"{rel}/plugin.json: version {pj['version']!r} is out of sync with the "
            f"catalog version(s) {sorted(versions)}"
        )

    if not os.path.isdir(os.path.join(pdir, "skills")):
        err(f"{rel}: plugin has no skills/ directory")
    if not os.path.isfile(os.path.join(pdir, "README.md")):
        err(f"{rel}: plugin has no README.md")
    return True


def plugin_dirs():
    """Names of the plugin directories under plugins/."""
    pdir = os.path.join(ROOT, "plugins")
    if not os.path.isdir(pdir):
        return []
    return sorted(
        d for d in os.listdir(pdir)
        if os.path.isdir(os.path.join(pdir, d)) and not d.startswith(".")
    )


def discover_skills():
    """Find every skill and enforce the one-skill-one-plugin layout.

    Skills live only at plugins/<plugin>/skills/<skill>/, as real directories.
    A symlink or a second same-named directory would put a skill in two
    plugins, so anyone installing both would load it twice.
    """
    if os.path.isdir(os.path.join(ROOT, "skills")):
        err(
            "skills/ exists at the repo root. Skills live under "
            "plugins/<plugin>/skills/<skill>/ — move it into the plugin it belongs to."
        )
    plugins = plugin_dirs()
    if not plugins:
        err("plugins/ contains no plugins")
    for p in plugins:
        sdir = os.path.join(ROOT, "plugins", p, "skills")
        if not os.path.isdir(sdir):
            continue
        entries = sorted(f for f in os.listdir(sdir) if not f.startswith("."))
        if not entries:
            err(f"plugins/{p}/skills: plugin contains no skills")
        for f in entries:
            path = os.path.join(sdir, f)
            rel = os.path.relpath(path, ROOT)
            if os.path.islink(path):
                err(f"{rel}: must be a real directory, not a symlink — each skill lives in one plugin")
                continue
            if not os.path.isdir(path):
                err(
                    f"{rel}: loose file under a plugin's skills/; every entry there "
                    f"must be a skill directory"
                )
                continue
            if f in SKILL_DIRS:
                err(f"{rel}: skill {f!r} already exists at {SKILL_DIRS[f]} — a skill belongs to exactly one plugin")
                continue
            SKILL_DIRS[f] = rel
    return sorted(SKILL_DIRS)


def check_starter_kit_pins():
    """A starter kit that installs from an external catalog must use its pinned ref.

    Kits reference upstream catalogs by commit so a project gets exactly what this
    catalog reviewed. Any 40-char SHA on a line of a kit file that mentions a
    registered catalog's repository must equal that catalog's sourceRef; moving
    the pin means updating the registry and the kit together. The check is per
    line so that one README can pin several catalogs.
    """
    kits = os.path.join(ROOT, "starter-kits")
    reg_path = os.path.join(ROOT, "tools", "external-catalogs.json")
    if not os.path.isdir(kits) or not os.path.isfile(reg_path):
        return
    try:
        catalogs = json.load(open(reg_path, encoding="utf-8")).get("catalogs", [])
    except json.JSONDecodeError:
        return  # reported by check_external_catalogs
    pins = {}
    for c in catalogs:
        m = re.match(r"https://github\.com/([^/]+/[^/]+?)/?$", c.get("repository", ""))
        if m and c.get("sourceRefType") == "commit":
            pins[m.group(1)] = (c.get("name"), c.get("sourceRef"))

    for dirpath, _, filenames in os.walk(kits):
        for fn in filenames:
            if not fn.endswith(TEXT_EXT):
                continue
            path = os.path.join(dirpath, fn)
            body = open(path, encoding="utf-8", errors="replace").read()
            for i, line in enumerate(body.splitlines(), 1):
                for slug, (cname, ref) in pins.items():
                    if slug not in line:
                        continue
                    for sha in set(re.findall(r"\b[0-9a-f]{40}\b", line)):
                        if sha != ref:
                            err(
                                f"{os.path.relpath(path, ROOT)}:{i}: pins {slug} at {sha[:12]}..., "
                                f"but tools/external-catalogs.json ({cname}) pins {ref[:12]}... — "
                                f"move both together"
                            )


def check_starter_kits():
    """A starter kit must enable plugins that this marketplace actually offers.

    Kits compose plugins through template/.github/copilot/settings.json. A typo
    there fails silently for every project created from the kit.
    """
    kits = os.path.join(ROOT, "starter-kits")
    if not os.path.isdir(kits):
        return
    mkt = os.path.join(ROOT, ".github", "plugin", "marketplace.json")
    try:
        m = json.load(open(mkt, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return  # reported by check_manifests
    offered = {f"{p.get('name')}@{m.get('name')}" for p in m.get("plugins", [])}

    for k in sorted(os.listdir(kits)):
        kdir = os.path.join(kits, k)
        if not os.path.isdir(kdir) or k.startswith("."):
            continue
        rel = os.path.relpath(kdir, ROOT)
        if not os.path.isfile(os.path.join(kdir, "README.md")):
            err(f"{rel}: starter kit has no README.md")
        sp = os.path.join(kdir, "template", ".github", "copilot", "settings.json")
        if not os.path.isfile(sp):
            err(f"{rel}: starter kit has no template/.github/copilot/settings.json")
            continue
        try:
            settings = json.load(open(sp, encoding="utf-8"))
        except json.JSONDecodeError as e:
            err(f"{os.path.relpath(sp, ROOT)}: invalid JSON — {e}")
            continue
        enabled = settings.get("enabledPlugins")
        if not isinstance(enabled, dict) or not enabled:
            err(f"{os.path.relpath(sp, ROOT)}: enabledPlugins must be a non-empty map of plugin@marketplace -> true")
            continue
        for key in enabled:
            if key.endswith(f"@{m.get('name')}") and key not in offered:
                err(f"{os.path.relpath(sp, ROOT)}: enables {key!r}, which the marketplace does not offer")


def check_external_tools():
    """Lint tools/external-tools.json and enforce it as the source of truth.

    The registry is what makes external tools machine-discoverable, so it is
    validated strictly, and any pointer skill must agree with its pinned ref.
    """
    path = os.path.join(ROOT, "tools", "external-tools.json")
    if not os.path.isfile(path):
        return  # optional file

    try:
        reg = json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(f"tools/external-tools.json: invalid JSON — {e}")
        return

    tools = reg.get("tools")
    if not isinstance(tools, list) or not tools:
        err("tools/external-tools.json: 'tools' must be a non-empty array")
        return

    required = ("name", "description", "repository", "license", "author", "install")
    seen = set()

    for t in tools:
        n = t.get("name", "<unnamed>")
        for f in required:
            if not t.get(f):
                err(f"external tool {n!r}: missing required field {f!r}")

        if n in seen:
            err(f"external tool {n!r}: duplicate entry")
        seen.add(n)

        repo = t.get("repository", "")
        if repo and not repo.startswith("https://github.com/"):
            err(f"external tool {n!r}: repository must be an https://github.com/ URL")

        # Pointers must never silently become vendored code.
        if t.get("bundled") is not False:
            err(f"external tool {n!r}: 'bundled' must be false — this catalog vendors no third-party code")

        # Anything that installs and executes code must be consent-gated.
        if t.get("requiresConfirmation") is not True:
            err(f"external tool {n!r}: 'requiresConfirmation' must be true for external tools")

        install = t.get("install") or {}
        ref = install.get("sourceRef")
        ref_type = install.get("sourceRefType")
        if not ref:
            err(f"external tool {n!r}: install.sourceRef is required — pin to a tag or commit")
        elif ref_type not in ("commit", "tag"):
            # Same trap as the catalog registry: guarding the SHA check behind
            # `== "commit"` lets an unlabelled branch name through untouched.
            err(
                f"external tool {n!r}: install.sourceRefType must be 'commit' or 'tag', "
                f"got {ref_type!r} — an unlabelled ref cannot be checked for immutability"
            )
        elif ref_type == "commit" and not re.fullmatch(r"[0-9a-f]{40}", ref):
            err(f"external tool {n!r}: install.sourceRef must be a full 40-char commit SHA")
        if not install.get("steps"):
            err(f"external tool {n!r}: install.steps is required")
        if not install.get("verify"):
            err(f"external tool {n!r}: install.verify is required")

        # These entries are pointers, not endorsements — a tool is NOT required
        # to have been installed and run before it is listed. What is required
        # is that the entry says which it is, so nobody mistakes a read of the
        # upstream tree for an observed working install.
        verified = t.get("verified") or {}
        scope = verified.get("scope")
        if scope not in ("tested", "metadata-only"):
            err(
                f"external tool {n!r}: verified.scope must be 'tested' (pinned install "
                f"run and install.verify succeeded) or 'metadata-only' (derived from "
                f"reading the pinned tree, nothing executed) — got {scope!r}"
            )
        if not verified.get("method"):
            err(f"external tool {n!r}: verified.method is required — say what you actually did")

        # A referenced pointer skill must exist and agree on the pinned ref.
        rel_skill = t.get("relatedSkill")
        if rel_skill:
            sdir = SKILL_DIRS.get(rel_skill)
            sp = os.path.join(ROOT, sdir, "SKILL.md") if sdir else ""
            if not sdir or not os.path.isfile(sp):
                err(f"external tool {n!r}: relatedSkill {rel_skill!r} does not exist")
            elif ref:
                body = open(sp, encoding="utf-8").read()
                if ref not in body:
                    err(
                        f"external tool {n!r}: pinned sourceRef {ref[:12]}... is not present in "
                        f"{sdir}/SKILL.md — the registry and the skill have drifted"
                    )
                if repo and repo not in body:
                    err(
                        f"external tool {n!r}: {sdir}/SKILL.md does not link the "
                        f"upstream repository"
                    )

    print(f"external tools OK: {', '.join(sorted(seen))}")


def check_external_catalogs():
    """Lint tools/external-catalogs.json and enforce it as the source of truth.

    An external *catalog* is a third-party collection of agent skills, not an
    installable CLI. Nothing lands on PATH and there is no `--version` to run,
    so the tools registry's install.* contract does not apply and these get
    their own file rather than a conditional branch inside check_external_tools.

    The threat model is also broader than a binary's: every SKILL.md is a prompt
    the user's agent will load and follow, and skills may ship scripts it will
    execute. So consent gating is validated here the same way, and the entry is
    additionally required to carry explicit safety guidance.
    """
    path = os.path.join(ROOT, "tools", "external-catalogs.json")
    if not os.path.isfile(path):
        return  # optional file

    # tools/ is outside the per-skill walk, so nothing else JSON-lints this file.
    try:
        reg = json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(f"tools/external-catalogs.json: invalid JSON — {e}")
        return

    catalogs = reg.get("catalogs")
    if not isinstance(catalogs, list) or not catalogs:
        err("tools/external-catalogs.json: 'catalogs' must be a non-empty array")
        return

    readme_path = os.path.join(ROOT, "README.md")
    readme = (
        open(readme_path, encoding="utf-8").read()
        if os.path.isfile(readme_path)
        else ""
    )

    required = ("name", "description", "repository", "license", "author", "format")
    seen = set()

    for c in catalogs:
        n = c.get("name", "<unnamed>")
        for f in required:
            if not c.get(f):
                err(f"external catalog {n!r}: missing required field {f!r}")

        if n in seen:
            err(f"external catalog {n!r}: duplicate entry")
        seen.add(n)

        if not NAME_RE.match(n):
            err(f"external catalog {n!r}: name must be lowercase-hyphenated")

        # Pointers must never silently become vendored content.
        if c.get("bundled") is not False:
            err(
                f"external catalog {n!r}: 'bundled' must be false — this catalog "
                f"vendors no third-party content"
            )

        # A catalog ships prompts and scripts the agent will load and run.
        if c.get("requiresConfirmation") is not True:
            err(
                f"external catalog {n!r}: 'requiresConfirmation' must be true for "
                f"external catalogs"
            )

        safety = c.get("safety") or {}
        if safety.get("autoInstall") is not False:
            err(
                f"external catalog {n!r}: safety.autoInstall must be false — agents "
                f"must never install a third-party skill catalog unattended"
            )
        if not safety.get("notes"):
            err(
                f"external catalog {n!r}: safety.notes is required — record what an "
                f"agent and a human each need to know before loading these skills"
            )

        # `repository` is the authoritative upstream. A read-only distribution
        # mirror (e.g. an OSTI GitLab) goes under `mirror`, so that neither the
        # authority nor the URL users actually browse is lost.
        repo = c.get("repository", "")
        if repo and not repo.startswith("https://"):
            err(f"external catalog {n!r}: repository must be an https:// URL")

        urls = [repo] if repo else []

        mirror = c.get("mirror")
        if mirror is not None:
            if not isinstance(mirror, dict) or not mirror.get("url"):
                err(f"external catalog {n!r}: 'mirror' must be an object with a 'url'")
            else:
                urls.append(mirror["url"])
                if mirror["url"].rstrip("/") == repo.rstrip("/"):
                    err(
                        f"external catalog {n!r}: mirror.url duplicates repository; "
                        f"drop 'mirror' if there is only one location"
                    )
                _check_ref(n, mirror, "mirror.sourceRef")

        _check_ref(n, c, "sourceRef")

        fmt = c.get("format") or {}
        if fmt and fmt.get("ghSkillInstallable") is None:
            err(
                f"external catalog {n!r}: format.ghSkillInstallable is required — "
                f"whether a catalog can be installed with 'gh skill install' is the "
                f"first thing a user needs to know, and leaving it unstated invites "
                f"them to guess. Check the layout against the discovery conventions "
                f"'gh skill' actually supports; do not assume a nested catalog is "
                f"excluded, because scoped layouts are supported."
            )

        # The README is the human-facing half of this registry. Require EVERY
        # declared location to be linked, not just one: an "any of" rule would
        # let the mirror — the URL users are actually pointed at — silently drop
        # out while the upstream link alone kept CI green.
        if readme and urls:
            unlinked = [u for u in urls if u.rstrip("/") not in readme]
            if unlinked:
                err(
                    f"external catalog {n!r}: README.md does not link "
                    f"{', '.join(unlinked)}. Both the authoritative upstream and any "
                    f"mirror must stay discoverable from the README — which one is "
                    f"foregrounded is a presentation choice, dropping one is not."
                )

    print(f"external catalogs OK: {', '.join(sorted(seen))}")


def _check_ref(name, obj, label):
    """A pinned ref must be immutable: a full commit SHA or a tag, never a branch.

    The type is required, not inferred. Guarding the SHA check behind
    `sourceRefType == "commit"` would mean `{"sourceRef": "main"}` with the type
    omitted passes silently — which is exactly the moving ref the pin exists to
    forbid. Absence of a claim is not a claim of absence.
    """
    ref = obj.get("sourceRef")
    if not ref:
        err(
            f"external catalog {name!r}: {label} is required — pin to a tag or "
            f"commit so a citation names an exact revision"
        )
        return
    ref_type = obj.get("sourceRefType")
    if ref_type not in ("commit", "tag"):
        err(
            f"external catalog {name!r}: {label}Type must be 'commit' or 'tag', "
            f"got {ref_type!r} — an unlabelled ref cannot be checked for "
            f"immutability, and a branch is never an acceptable pin"
        )
        return
    if ref_type == "commit" and not re.fullmatch(r"[0-9a-f]{40}", ref):
        err(f"external catalog {name!r}: {label} must be a full 40-char commit SHA")


def check_readme(skill_names):
    """The README skills table must list every skill, with its category.

    The taxonomy's source of truth is each SKILL.md. The README is a view of it,
    so it is validated rather than hand-maintained in parallel — a second
    hand-authored source of truth would drift on the first busy day.
    """
    path = os.path.join(ROOT, "README.md")
    if not os.path.isfile(path):
        err("README.md: missing")
        return
    body = open(path, encoding="utf-8").read()

    missing = [n for n in skill_names if f"{SKILL_DIRS[n]})" not in body]
    if missing:
        err(
            f"README.md does not link skill(s): {', '.join(missing)}. "
            f"Every skill must appear in the skills table, linked to its plugin path."
        )

    for p in plugin_dirs():
        if f"(plugins/{p})" not in body:
            err(f"README.md: Plugins table does not link plugins/{p}")

    for category in sorted({c for c in SKILL_CATEGORIES.values() if c}):
        if category not in body:
            err(f"README.md: no section or row for category {category!r}")

    print(f"README lists all {len(skill_names)} skill(s)")


def main():
    found = discover_skills()
    for d in found:
        check_skill(os.path.join(ROOT, SKILL_DIRS[d]))
    print(f"checked {len(found)} skill(s): {', '.join(found)}")
    if found:
        check_readme(found)

    check_manifests()
    check_external_tools()
    check_external_catalogs()
    check_starter_kits()
    check_starter_kit_pins()

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    if errors:
        print(f"\n{len(errors)} error(s)")
        sys.exit(1)
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
