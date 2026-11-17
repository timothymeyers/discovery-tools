"""Shared pytest fixtures for the biocyc-network-context skill's offline
test suite. Adds `scripts/` to `sys.path` so tests can `import` the skill's
modules directly, matching the sabio-rk-kinetics skill's test layout."""
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
