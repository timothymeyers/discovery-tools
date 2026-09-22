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

errors = []
warnings = []


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

    if os.path.isdir(os.path.join(skill_dir, "reference")):
        err(f"{rel}: use 'references/' (plural), not 'reference/'")

    # --- repo safety rules ---
    for dirpath, _, filenames in os.walk(skill_dir):
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

    if a != b:
        err(
            ".github/plugin/marketplace.json and .claude-plugin/marketplace.json differ; "
            "run: cp .github/plugin/marketplace.json .claude-plugin/marketplace.json"
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
        warn(f"version fields are out of sync across manifests: {sorted(versions)}")

    for s in pj.get("skills", []):
        if not os.path.isdir(os.path.join(ROOT, s.rstrip("/"))):
            err(f"plugin.json: declared skills path {s!r} does not exist")

    if "agents" in pj:
        ad = os.path.join(ROOT, pj["agents"].rstrip("/"))
        has_agents = os.path.isdir(ad) and any(
            f.endswith(".agent.md") for f in os.listdir(ad)
        )
        if not has_agents:
            err(f"plugin.json: declares agents at {pj['agents']!r} but none were found")

    print("manifests OK")


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
        if not ref:
            err(f"external tool {n!r}: install.sourceRef is required — pin to a tag or commit")
        elif install.get("sourceRefType") == "commit" and not re.fullmatch(r"[0-9a-f]{40}", ref):
            err(f"external tool {n!r}: install.sourceRef must be a full 40-char commit SHA")
        if not install.get("steps"):
            err(f"external tool {n!r}: install.steps is required")
        if not install.get("verify"):
            err(f"external tool {n!r}: install.verify is required")

        # A referenced pointer skill must exist and agree on the pinned ref.
        rel_skill = t.get("relatedSkill")
        if rel_skill:
            sp = os.path.join(ROOT, "skills", rel_skill, "SKILL.md")
            if not os.path.isfile(sp):
                err(f"external tool {n!r}: relatedSkill {rel_skill!r} does not exist")
            elif ref:
                body = open(sp, encoding="utf-8").read()
                if ref not in body:
                    err(
                        f"external tool {n!r}: pinned sourceRef {ref[:12]}... is not present in "
                        f"skills/{rel_skill}/SKILL.md — the registry and the skill have drifted"
                    )
                if repo and repo not in body:
                    err(
                        f"external tool {n!r}: skills/{rel_skill}/SKILL.md does not link the "
                        f"upstream repository"
                    )

    print(f"external tools OK: {', '.join(sorted(seen))}")


def main():
    skills_root = os.path.join(ROOT, "skills")
    if not os.path.isdir(skills_root):
        err("skills/ directory is missing")
    else:
        found = sorted(
            d
            for d in os.listdir(skills_root)
            if os.path.isdir(os.path.join(skills_root, d)) and not d.startswith(".")
        )
        if not found:
            err("skills/ contains no skills")
        for d in found:
            check_skill(os.path.join(skills_root, d))
        print(f"checked {len(found)} skill(s): {', '.join(found)}")

    check_manifests()
    check_external_tools()

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
