"""Shared pytest fixtures/helpers for the sabio-rk-kinetics skill's offline
test suite. No live network access anywhere in this directory -- every HTTP
interaction is injected via a fake `http_get` callable reading from
`../fixtures/`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
FIXTURES_DIR = SKILL_ROOT / "fixtures"

sys.path.insert(0, str(SCRIPTS_DIR))


def load_fixture(name: str):
    path = FIXTURES_DIR / name
    if path.suffix == ".xml":
        return path.read_bytes()
    return json.loads(path.read_text(encoding="utf-8"))


class FakeClock:
    """Deterministic, instant-advancing clock/sleep pair for RateLimiter tests."""

    def __init__(self, start: float = 0.0):
        self.now = start
        self.sleep_calls = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock():
    return FakeClock()
