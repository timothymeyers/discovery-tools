"""Offline tests for the opt-in live contract probe. These tests never make
a real network call: they verify the consent gate, the minimum-spacing
guardrail, the small hard request cap, and route-status classification,
using an injected fake HTTP layer.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from biocyc_client import BioCycClient, HttpResponse, RateLimiter

_PROBE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "live_contract_probe.py"
_PROBE_SPEC = importlib.util.spec_from_file_location("biocyc_live_contract_probe", _PROBE_PATH)
assert _PROBE_SPEC and _PROBE_SPEC.loader
probe = importlib.util.module_from_spec(_PROBE_SPEC)
_PROBE_SPEC.loader.exec_module(probe)


def test_refuses_to_run_without_consent(capsys):
    exit_code = probe.run([])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ran"] is False


def test_refuses_to_run_below_minimum_spacing(capsys):
    exit_code = probe.run([
        "--i-understand-this-makes-a-live-request",
        "--min-spacing-seconds", "0.1",
    ])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ran"] is False
    assert "1.0" in out["reason"] or "1 query/sec" in out["reason"]


def test_classify_route_status_reachable_public():
    assert probe.classify_route_status("ok", 200) == "reachable_public"


def test_classify_route_status_gated():
    assert probe.classify_route_status("gated", 404) == "gated_paid_subscription"


def test_classify_route_status_missing_frame():
    assert probe.classify_route_status("missing_frame", 404) == "dead_superseded"


def test_classify_route_status_unknown_when_outcome_missing():
    assert probe.classify_route_status(None, None) == "unknown_untested"


def test_runs_with_consent_using_injected_client(monkeypatch, capsys):
    def fake_http_get(url):
        return HttpResponse(200, b"<ptools-xml ptools-version='30.0'><Reaction/></ptools-xml>", {})

    def fake_biocyc_client(*args, **kwargs):
        return BioCycClient(http_get=fake_http_get, rate_limiter=RateLimiter(min_spacing_seconds=0.0))

    monkeypatch.setattr(probe, "BioCycClient", fake_biocyc_client)
    exit_code = probe.run(["--i-understand-this-makes-a-live-request", "--max-requests", "1"])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ran"] is True
    assert out["requests_issued"] == 1
    assert out["bulk_download_attempted"] is False
    assert out["results"][0]["route_status"] == "reachable_public"


def test_max_requests_is_hard_capped(monkeypatch):
    calls = {"n": 0}

    def fake_http_get(url):
        calls["n"] += 1
        return HttpResponse(200, b"<ptools-xml/>", {})

    def fake_biocyc_client(*args, **kwargs):
        return BioCycClient(http_get=fake_http_get, rate_limiter=RateLimiter(min_spacing_seconds=0.0))

    monkeypatch.setattr(probe, "BioCycClient", fake_biocyc_client)
    probe.run(["--i-understand-this-makes-a-live-request", "--max-requests", "999"])
    assert calls["n"] <= probe.MAX_REQUESTS_HARD_CAP


def test_probe_never_targets_organism_specific_orgid_by_default():
    """The default probe target is a public Tier-1 MetaCyc frame, not an
    organism-specific (Tier-2/3) PGDB -- this is a reachability check, not
    an ingestion of any specific organism's data."""
    assert probe.DEFAULT_PROBE_ORGID == "META"
