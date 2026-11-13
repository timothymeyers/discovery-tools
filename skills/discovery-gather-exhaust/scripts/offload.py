#!/usr/bin/env python3
"""Large-file handling for discovery-gather-exhaust.

Choices for a gathered file over the warn threshold:
  archive  gzip in place (path.gz) and keep it in plain git
  lfs      track that exact path with Git LFS (.gitattributes)
  blob     upload to Azure Blob Storage, commit a small pointer file
  skip     do not copy; recorded in the manifest only
  git      keep as-is in plain git (refused above the hard limit)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

WARN_BYTES = 50 * 1024 * 1024
HARD_BYTES = 100 * 1024 * 1024
CHOICES = ("archive", "lfs", "blob", "skip", "git")


class OffloadError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive(dest: Path, data: bytes) -> Path:
    out = dest.with_name(dest.name + ".gz")
    out.parent.mkdir(parents=True, exist_ok=True)
    gz = gzip.compress(data, compresslevel=9, mtime=0)
    if len(gz) > HARD_BYTES:
        raise OffloadError(f"{out.name} is still {len(gz) // 2**20} MB after gzip; choose lfs or blob")
    out.write_bytes(gz)
    return out


def lfs_track(repo: Path, rel: str) -> None:
    if shutil.which("git-lfs") is None:
        raise OffloadError("git-lfs is not installed (brew install git-lfs)")
    subprocess.run(["git", "lfs", "install", "--local"], cwd=repo, check=True, capture_output=True)
    attrs = repo / ".gitattributes"
    line = f"{rel.replace(' ', '[[:space:]]')} filter=lfs diff=lfs merge=lfs -text"
    existing = attrs.read_text().splitlines() if attrs.exists() else []
    if line not in existing:
        with attrs.open("a") as fh:
            fh.write(("\n" if existing and existing[-1] else "") + line + "\n")


def blob_upload(src_data: bytes, dest: Path, rel: str, account: str, container: str) -> Path:
    if shutil.which("az") is None:
        raise OffloadError("Azure CLI (az) is not installed")
    if subprocess.run(["az", "account", "show"], capture_output=True).returncode != 0:
        raise OffloadError("Azure CLI is not signed in; run `az login`")
    tmp = dest.with_name(dest.name + ".upload.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(src_data)
    try:
        r = subprocess.run(
            ["az", "storage", "blob", "upload", "--auth-mode", "login", "--overwrite",
             "--account-name", account, "--container-name", container,
             "--name", rel, "--file", str(tmp), "--only-show-errors"],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise OffloadError(f"blob upload failed: {r.stderr.strip()[:400]}")
    finally:
        tmp.unlink(missing_ok=True)
    pointer = dest.with_name(dest.name + ".blob.json")
    pointer.write_text(json.dumps({
        "url": f"https://{account}.blob.core.windows.net/{container}/{rel}",
        "sha256": sha256_bytes(src_data), "size": len(src_data),
    }, indent=2) + "\n")
    return pointer


def apply_choice(choice: str, *, repo: Path, dest: Path, rel_repo: str, data: bytes,
                 blob_cfg: dict | None = None) -> tuple[str, Path | None]:
    """Write `data` for `dest` according to `choice`. Returns (status, written_path)."""
    if choice not in CHOICES:
        raise OffloadError(f"unknown large-file choice {choice!r}")
    if choice == "skip":
        return "skipped-large", None
    if choice == "archive":
        return "archived", archive(dest, data)
    if choice == "blob":
        cfg = blob_cfg or {}
        if not cfg.get("account") or not cfg.get("container"):
            raise OffloadError("blob choice needs config.json blob.account and blob.container")
        return "blob", blob_upload(data, dest, rel_repo, cfg["account"], cfg["container"])
    if choice == "git" and len(data) > HARD_BYTES:
        raise OffloadError(f"{dest.name} is over 100 MB; plain git is refused (GitHub limit)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    if choice == "lfs":
        lfs_track(repo, rel_repo)
        return "lfs", dest
    return "written", dest
