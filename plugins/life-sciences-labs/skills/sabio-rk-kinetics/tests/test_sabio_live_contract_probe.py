"""Offline tests for the opt-in live contract probe. These tests never make
a real network call: they verify the consent-gate refuses to run without
explicit opt-in, and that `classify_route_status` maps HTTP outcomes onto
the shared route-status vocabulary correctly, using an injected fake client.
"""
from __future__ import annotations

import json
import importlib.util
from pathlib import Path

from sabio_rk_client import HttpResponse, RateLimiter, SabioClient

_PROBE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "live_contract_probe.py"
_PROBE_SPEC = importlib.util.spec_from_file_location("sabio_live_contract_probe", _PROBE_PATH)
assert _PROBE_SPEC and _PROBE_SPEC.loader
probe = importlib.util.module_from_spec(_PROBE_SPEC)
_PROBE_SPEC.loader.exec_module(probe)


def test_refuses_to_run_without_consent(capsys):
    exit_code = probe.run([])
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ran"] is False


def test_classify_route_status_reachable_public():
    assert probe.classify_route_status(200, "application/json", {"meta": {}, "data": []}) == "reachable_public"


def test_classify_route_status_gated_credentialed():
    assert probe.classify_route_status(403, None, None) == "gated_credentialed"


def test_classify_route_status_dead_superseded():
    assert probe.classify_route_status(404, None, None) == "dead_superseded"


def test_classify_route_status_rate_limited_still_reachable():
    assert probe.classify_route_status(429, None, None) == "reachable_public"


def test_classify_route_status_unknown_when_status_missing():
    assert probe.classify_route_status(None, None, None) == "unknown_untested"


def test_runs_with_consent_flag_using_injected_client(monkeypatch, capsys):
    def fake_sabio_client(*args, **kwargs):
        def fake_http_get(url):
            return HttpResponse(200, json.dumps({"meta": {"total_count": 0}, "data": []}).encode("utf-8"), {})

        return SabioClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=60.0))

    monkeypatch.setattr(probe, "SabioClient", fake_sabio_client)
    exit_code = probe.run(["--i-understand-this-makes-a-live-request", "--max-requests", "1"])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ran"] is True
    assert out["requests_issued"] == 1
    assert out["results"][0]["route_status"] == "reachable_public"


def test_max_requests_is_hard_capped(monkeypatch, capsys):
    calls = {"n": 0}

    def fake_sabio_client(*args, **kwargs):
        def fake_http_get(url):
            calls["n"] += 1
            return HttpResponse(200, json.dumps({"meta": {}, "data": []}).encode("utf-8"), {})

        return SabioClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=60.0))

    monkeypatch.setattr(probe, "SabioClient", fake_sabio_client)
    probe.run(["--i-understand-this-makes-a-live-request", "--max-requests", "999"])
    assert calls["n"] <= probe.MAX_REQUESTS_HARD_CAP
