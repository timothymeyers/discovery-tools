"""Offline tests for live_contract_probe.py's consent-gating and
license-safety guardrails. No real network call is made in this file --
`probe(consent=False)` refuses before any HTTP request would be issued, and
the forbidden-parameter assertion is exercised without live network access."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))
_PROBE_SPEC = importlib.util.spec_from_file_location(
    "brenda_live_contract_probe", _SCRIPTS_DIR / "live_contract_probe.py"
)
assert _PROBE_SPEC and _PROBE_SPEC.loader
live_contract_probe = importlib.util.module_from_spec(_PROBE_SPEC)
_PROBE_SPEC.loader.exec_module(live_contract_probe)


def test_probe_refuses_without_consent():
    result = live_contract_probe.probe(consent=False)
    assert result["ran"] is False
    assert "consent" in result["reason"]


def test_probe_urls_never_contain_license_acceptance_or_download_params():
    for url in (
        live_contract_probe.SOAP_WSDL_URL,
        live_contract_probe.DOWNLOAD_PAGE_URL,
        live_contract_probe.SPARQL_PROBE_URL,
    ):
        assert "accept-license" not in url
        assert "dlfile" not in url


def test_probe_module_never_reads_brenda_credential_env_vars(monkeypatch):
    monkeypatch.setenv("BRENDA_EMAIL", "should-not-be-read@example.com")
    monkeypatch.setenv("BRENDA_PASSWORD_SHA256", "deadbeef")
    result = live_contract_probe.probe(consent=False)
    assert "BRENDA_EMAIL" not in str(result)
    assert "should-not-be-read" not in str(result)


def test_probe_forbidden_param_assertion_would_trip_on_a_tampered_url(monkeypatch):
    monkeypatch.setattr(live_contract_probe, "DOWNLOAD_PAGE_URL", "https://example.com/download.php?accept-license=1")
    with pytest.raises(AssertionError):
        live_contract_probe.probe(consent=True)


def test_classify_route_status_maps_known_http_codes():
    assert live_contract_probe.classify_route_status(200) == "reachable_public"
    assert live_contract_probe.classify_route_status(403) == "gated_credentialed"
    assert live_contract_probe.classify_route_status(404) == "dead_superseded"
    assert live_contract_probe.classify_route_status(None) == "unknown_untested"
