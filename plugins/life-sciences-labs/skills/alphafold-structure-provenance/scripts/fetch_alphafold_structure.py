#!/usr/bin/env python3
"""Version-aware AlphaFold DB structure fetch.

For each accession, first calls the AlphaFold DB prediction-metadata API to
resolve the CURRENT `latestVersion` (never trust a version number cached
from a prior run), then downloads the structure/confidence/PAE files using
that resolved version number. Records, per accession, whether a previously
recorded source version still matches the current latestVersion, so any
downstream derived quantity stays traceable to the exact structure version
it came from.

IMPORTANT: `allVersions` in the API response is historical metadata only --
it is NOT a list of currently-downloadable file versions. Only the current
`latestVersion` file is reliably fetchable; older version numbers commonly
404. This script never constructs a file URL from anything other than the
freshly-resolved `latestVersion`.

Example:
    python fetch_alphafold_structure.py --accession P69905 \\
        --cache-dir af_cache --prior-version 4
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://alphafold.ebi.ac.uk/api/prediction/{acc}"
FILE_URL = "https://alphafold.ebi.ac.uk/files/AF-{acc}-F1-{artifact}_v{version}.{ext}"
USER_AGENT = "alphafold-structure-provenance-skill/1.0 (contact: discovery-workspace)"

ARTIFACTS = {
    "model": ("model", "cif"),
    "confidence": ("confidence", "json"),
    "pae": ("predicted_aligned_error", "json"),
}


def http_get(url: str, timeout: float = 30.0) -> tuple[int, bytes | None]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None


def fetch_metadata(acc: str, cache_dir: Path) -> dict:
    cache_path = cache_dir / f"{acc}_prediction.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text())
    else:
        status, raw = http_get(API_URL.format(acc=acc))
        if status != 200 or raw is None:
            raise SystemExit(f"AlphaFold prediction metadata fetch failed for {acc}: HTTP {status}")
        payload = json.loads(raw)
        cache_path.write_text(json.dumps(payload, indent=1))
    entry = payload[0] if isinstance(payload, list) and payload else payload
    return entry


def resolve_latest_version(entry: dict, accession: str) -> int | str:
    """Validate and return the authoritative download version from a
    prediction-metadata entry.

    Raises SystemExit if `latestVersion` is missing or is not a usable
    version identifier (e.g. `None`, empty string, or a non-integer-like
    value). `allVersions` is deliberately never consulted here -- it is
    historical metadata only and must never drive a download filename.
    """
    latest_version = entry.get("latestVersion")
    if latest_version is None or latest_version == "":
        raise SystemExit(f"No latestVersion field in AlphaFold response for {accession}")
    try:
        int(latest_version)
    except (TypeError, ValueError):
        raise SystemExit(
            f"Malformed latestVersion field in AlphaFold response for {accession}: "
            f"{latest_version!r} is not a valid version number"
        )
    return latest_version


def run(accession: str, cache_dir: Path, artifacts: list[str],
        prior_version: str | None = None, sleep_seconds: float = 0.2) -> dict:
    """Resolve latestVersion and fetch the requested artifact files for one
    accession. Returns a summary dict (used by tests and callers); also
    prints the same human-readable provenance lines as the CLI.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    entry = fetch_metadata(accession, cache_dir)
    all_versions = entry.get("allVersions", [])
    latest_version = resolve_latest_version(entry, accession)

    print(f"Accession: {accession}")
    print(f"latestVersion (authoritative for download): {latest_version}")
    print(f"allVersions (historical only, NOT a download list): {all_versions}")

    version_match = None
    if prior_version is not None:
        if str(prior_version) == str(latest_version):
            version_match = True
            print("model_version_exact_match: exact version still available")
        else:
            version_match = False
            print(
                f"model_version_exact_match: NO -- prior source used version "
                f"{prior_version}, AlphaFold DB now serves latestVersion "
                f"{latest_version}. Older version files are commonly not retrievable; "
                "do not assume newly-fetched files are identical in geometry to the "
                "original prior-version structure."
            )

    fetched: dict[str, dict] = {}
    for artifact_key in artifacts:
        artifact_name, ext = ARTIFACTS[artifact_key]
        out_path = cache_dir / f"AF-{accession}-F1-{artifact_name}_v{latest_version}.{ext}"
        if out_path.exists():
            print(f"  [{artifact_key}] already cached: {out_path}")
            fetched[artifact_key] = {"status": "cached", "path": str(out_path)}
            continue
        url = FILE_URL.format(acc=accession, artifact=artifact_name,
                               version=latest_version, ext=ext)
        status, raw = http_get(url)
        if status == 200 and raw is not None:
            out_path.write_bytes(raw)
            print(f"  [{artifact_key}] fetched v{latest_version} -> {out_path} (HTTP 200)")
            fetched[artifact_key] = {"status": "fetched", "http_status": status, "path": str(out_path)}
        else:
            print(f"  [{artifact_key}] fetch failed for v{latest_version}: HTTP {status}")
            fetched[artifact_key] = {"status": "failed", "http_status": status, "path": None}
        if sleep_seconds:
            time.sleep(sleep_seconds)

    return {
        "accession": accession,
        "latest_version": latest_version,
        "all_versions": all_versions,
        "version_match": version_match,
        "artifacts": fetched,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--accession", required=True)
    ap.add_argument("--cache-dir", default="alphafold_cache")
    ap.add_argument("--artifacts", nargs="+", default=["model", "confidence", "pae"],
                     choices=list(ARTIFACTS))
    ap.add_argument("--prior-version", default=None,
                     help="previously-recorded source structure version, for provenance comparison")
    args = ap.parse_args()

    run(args.accession, Path(args.cache_dir), args.artifacts, args.prior_version)


if __name__ == "__main__":
    main()
