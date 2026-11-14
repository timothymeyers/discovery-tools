#!/usr/bin/env bash
# Install the discovery-project-kit's upstream skills from microsoft/discovery,
# pinned to an exact commit, into this project.
#
# Third-party skills are prompts your agent will follow and scripts it may run.
# This script never installs anything without an explicit confirmation.
#
# Usage: scripts/install-upstream-skills.sh [--yes] [--agent <host>] [--force] [--dry-run]
#   --yes        skip the confirmation prompt (you have already reviewed the skills)
#   --agent      gh skill target host (default: github-copilot)
#   --force      overwrite an existing copy of the skill
#   --dry-run    print the commands without running them
set -euo pipefail

UPSTREAM_REPO="microsoft/discovery"
# Must match tools/external-catalogs.json -> microsoft-discovery.sourceRef (CI enforces this).
UPSTREAM_REF="d424c9e7e6b769fdfdbe0c63bea4ad8ff7a82ecf"
SKILLS=(
  "skills/token-estimator-mvp"
)

yes=0 force=0 dry=0 agent="github-copilot"
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) yes=1 ;;
    --force) force=1 ;;
    --dry-run) dry=1 ;;
    --agent) shift; agent="${1:?--agent needs a value}" ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "error: $*" >&2; exit 1; }

command -v gh >/dev/null || die "GitHub CLI (gh) 2.90.0 or later is required: https://cli.github.com"
gh_version="$(gh --version | head -1 | awk '{print $3}')"
if [ "$(printf '%s\n%s\n' "2.90.0" "$gh_version" | sort -V | head -1)" != "2.90.0" ]; then
  die "gh $gh_version is too old; gh skill needs 2.90.0 or later"
fi
git rev-parse --show-toplevel >/dev/null 2>&1 || die "run this from inside your project's git repository"
cd "$(git rev-parse --show-toplevel)"
command -v node >/dev/null || echo "warning: token-estimator-mvp runs on Node.js, which was not found on PATH" >&2

echo "This installs ${#SKILLS[@]} third-party skill(s) from $UPSTREAM_REPO @ ${UPSTREAM_REF:0:12} into $(pwd):"
for s in "${SKILLS[@]}"; do
  echo "  - $s"
  echo "    review first: gh skill preview $UPSTREAM_REPO ${s}@${UPSTREAM_REF}"
done

if [ "$yes" -ne 1 ]; then
  [ -t 0 ] || die "not a terminal; re-run with --yes after reviewing the skills"
  read -r -p "Install them? [y/N] " answer
  case "$answer" in y|Y|yes|YES) ;; *) echo "Nothing installed."; exit 0 ;; esac
fi

for s in "${SKILLS[@]}"; do
  cmd=(gh skill install "$UPSTREAM_REPO" "$s" --pin "$UPSTREAM_REF" --agent "$agent" --scope project)
  [ "$force" -eq 1 ] && cmd+=(--force)
  if [ "$dry" -eq 1 ]; then
    printf '%q ' "${cmd[@]}"; echo
  else
    "${cmd[@]}"
  fi
done
