"""Offline tests for `response_classifier.classify_response`: missing_frame
vs gated vs ok, using this skill's own bundled fixtures. No network calls."""
from pathlib import Path

from response_classifier import classify_response

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def test_missing_frame_classified_correctly():
    """A real 404 body shape for a syntactically valid but non-existent
    frame ID -- see DX-53's live-confirmed message text."""
    body = (FIXTURES_DIR / "missing_frame_response.xml").read_text()
    assert classify_response(404, body) == "missing_frame"
    assert "was not found in database ECOLI" in body


def test_gated_response_classified_correctly():
    """Synthetic fixture (never captured live) exercising the gated branch."""
    body = (FIXTURES_DIR / "synthetic_gated_response.html").read_text()
    assert classify_response(404, body) == "gated"


def test_ok_response_is_neither_missing_frame_nor_gated():
    body = (FIXTURES_DIR / "reaction_ec_5.3.1.9_pgi.xml").read_text()
    assert classify_response(200, body) == "ok"


def test_missing_frame_and_gated_are_distinguishable():
    missing = (FIXTURES_DIR / "missing_frame_response.xml").read_text()
    gated = (FIXTURES_DIR / "synthetic_gated_response.html").read_text()
    assert classify_response(404, missing) != classify_response(404, gated)


def test_generic_marketing_chrome_on_a_200_is_not_misclassified_as_gated():
    """A non-404 response must always classify as 'ok', even if it happens
    to mention subscription/login chrome somewhere in its body -- that
    distinction only applies to a genuine 404."""
    body = "<html>Please consider a BioCyc subscription. hCaptcha widget on other pages.</html>"
    assert classify_response(200, body) == "ok"


def test_404_without_either_signature_defaults_to_ok_not_silently_misclassified():
    """A 404 lacking both the missing-frame message and the hCaptcha marker
    should not be silently misreported as either specific outcome; per this
    classifier's conservative default it falls through to 'ok' so callers
    must widen their own signature set rather than trust a false-positive
    missing_frame/gated label."""
    assert classify_response(404, "<html>Some unrelated 404 page.</html>") == "ok"
