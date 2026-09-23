"""Offline tests for `RateLimiter`, `parse_retry_after_seconds`, and
`SabioClient` (rate limiting, HTTP-429/Retry-After retry, outcome/
failure_kind classification, response caching). No live network calls --
all HTTP is injected. Run: python -m pytest tests/ -q from the skill root.
"""
from __future__ import annotations

import json

from sabio_rk_client import HttpResponse, RateLimiter, SabioClient, parse_retry_after_seconds

from sabio_test_helpers import load_fixture


# --------------------------------------------------------------------------
# RateLimiter
# --------------------------------------------------------------------------
def test_rate_limiter_allows_burst_up_to_limit_without_sleeping(fake_clock):
    limiter = RateLimiter(max_requests=3, window=60.0, time_fn=fake_clock.time, sleep_fn=fake_clock.sleep)
    for _ in range(3):
        limiter.acquire()
    assert fake_clock.sleep_calls == []


def test_rate_limiter_sleeps_when_burst_exceeds_limit(fake_clock):
    limiter = RateLimiter(max_requests=2, window=60.0, time_fn=fake_clock.time, sleep_fn=fake_clock.sleep)
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()  # third call within the window must wait.
    assert len(fake_clock.sleep_calls) == 1
    assert fake_clock.sleep_calls[0] == 60.0


def test_rate_limiter_does_not_sleep_once_window_has_elapsed(fake_clock):
    limiter = RateLimiter(max_requests=1, window=60.0, time_fn=fake_clock.time, sleep_fn=fake_clock.sleep)
    limiter.acquire()
    fake_clock.now += 61.0
    limiter.acquire()
    assert fake_clock.sleep_calls == []


# --------------------------------------------------------------------------
# Retry-After parsing
# --------------------------------------------------------------------------
def test_parse_retry_after_seconds_integer_string():
    assert parse_retry_after_seconds({"Retry-After": "2"}) == 2.0


def test_parse_retry_after_seconds_case_insensitive_header_lookup():
    assert parse_retry_after_seconds({"retry-after": "5"}) == 5.0


def test_parse_retry_after_seconds_absent_returns_none():
    assert parse_retry_after_seconds({"Content-Type": "application/json"}) is None


def test_parse_retry_after_seconds_unparseable_returns_none():
    assert parse_retry_after_seconds({"Retry-After": "not-a-number-or-date"}) is None


# --------------------------------------------------------------------------
# SabioClient outcome/failure_kind semantics
# --------------------------------------------------------------------------
def _client_with_fake(responses, sleep_fn=None, cache_dir=None):
    calls = {"n": 0}

    def fake_http_get(url):
        resp = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return resp

    return SabioClient(
        http_get=fake_http_get,
        rate_limiter=RateLimiter(max_requests=1000, window=60.0),
        sleep_fn=sleep_fn or (lambda s: None),
        cache_dir=cache_dir,
    ), calls


def test_get_json_success_sets_outcome_success():
    fixture = load_fixture("tier1_exact_organism_hit.json")
    body = json.dumps(fixture).encode("utf-8")
    client, _ = _client_with_fake([HttpResponse(200, body, {})])
    parsed, meta = client.get_json("/kinlaw-entry/json", {"q": "ECNumber:1.1.1.1"})
    assert meta["outcome"] == "success"
    assert parsed["meta"]["total_count"] == 1


def test_get_json_429_honors_retry_after_then_succeeds():
    retry_fixture = load_fixture("outcome_429_retry_after.json")
    ok_fixture = load_fixture("tier1_exact_organism_hit.json")
    responses = [
        HttpResponse(429, json.dumps(retry_fixture["body"]).encode("utf-8"), retry_fixture["headers"]),
        HttpResponse(200, json.dumps(ok_fixture).encode("utf-8"), {}),
    ]
    sleeps = []
    client, calls = _client_with_fake(responses, sleep_fn=sleeps.append)
    parsed, meta = client.get_json("/kinlaw-entry/json", {"q": "ECNumber:1.1.1.1"})
    assert meta["outcome"] == "success"
    assert meta["attempts"] == 2
    assert sleeps == [2.0]  # exact Retry-After value, not a computed fallback.


def test_get_json_auth_failure_403():
    fixture = load_fixture("outcome_auth_failure.json")
    client, _ = _client_with_fake([HttpResponse(403, fixture["body"].encode("utf-8"), fixture["headers"])])
    parsed, meta = client.get_json("/kinlaw-entry/json", {"q": "ECNumber:1.1.1.1"})
    assert parsed is None
    assert meta["outcome"] == "failure"
    assert meta["failure_kind"] == "authorization"


def test_get_json_transport_failure_no_status():
    client, _ = _client_with_fake([HttpResponse(0, b"")])
    # Force the fake http_get to simulate "no response object at all" by
    # monkeypatching the private hook -- simplest is a client whose
    # http_get returns a response with an unrecognized non-2xx/3xx/4xx code.
    parsed, meta = client.get_json("/kinlaw-entry/json", {"q": "ECNumber:1.1.1.1"})
    assert parsed is None
    assert meta["outcome"] == "failure"
    assert meta["failure_kind"] == "transport"


def test_get_json_malformed_response_is_parser_failure():
    # Body is not valid JSON at all -> json.loads raises -> parser failure.
    client, _ = _client_with_fake([HttpResponse(200, b"not json { at all")])
    parsed, meta = client.get_json("/kinlaw-entry/json", {"q": "ECNumber:1.1.1.1"})
    assert parsed is None
    assert meta["outcome"] == "failure"
    assert meta["failure_kind"] == "parser"


def test_get_json_writes_cache_with_provenance(tmp_path):
    fixture = load_fixture("tier1_exact_organism_hit.json")
    body = json.dumps(fixture).encode("utf-8")
    client, _ = _client_with_fake([HttpResponse(200, body, {})], cache_dir=tmp_path)
    client.get_json("/kinlaw-entry/json", {"q": "ECNumber:1.1.1.1"}, cache_key="test_key")
    client.write_manifest()
    cached = json.loads((tmp_path / "test_key.json").read_text())
    assert cached["meta"]["outcome"] == "success"
    assert cached["meta"]["http_status"] == 200
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest[0]["cache_key"] == "test_key"
