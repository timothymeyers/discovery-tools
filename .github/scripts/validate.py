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
    """Minimal YAML frontmatter reader: top-level scalars and block scalars only."""
    if not text.startswith("---"):
        err(f"{path}: missing YAML frontmatter")
        return None
    end = text.find("\n---", 3)
    if end == -1:
        err(f"{path}: unterminated frontmatter")
        return None

    data, key, buf = {}, None, []
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            if key:
                data[key] = "\n".join(buf).strip()
            key = m.group(1)
            val = m.group(2).strip()
            buf = [] if val in ("|", ">", "|-", ">-", "") else [val]
        elif key:
            buf.append(line.strip())
    if key:
        data[key] = "\n".join(buf).strip()
    return data


def check_skill(skill_dir):
    name = os.path.basename(skill_dir)
    rel = os.path.relpath(skill_dir, ROOT)
    skill_md = os.path.join(skill_dir, "SKILL.md")

    if not os.path.isfile(skill_md):
        err(f"{rel}: no SKILL.md")
        return

    fm = parse_frontmatter(open(skill_md, encoding="utf-8").read(), f"{rel}/SKILL.md")
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

    if "version" in fm:
        warn(f"{rel}: 'version' is not in the Agent Skills spec; version via git tags")

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
