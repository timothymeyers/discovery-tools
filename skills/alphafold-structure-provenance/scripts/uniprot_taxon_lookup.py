#!/usr/bin/env python3
"""Taxon-scoped UniProt accession lookup.

Queries the UniProt REST search API (https://rest.uniprot.org/uniprotkb/search)
for every accession under a given NCBI taxonomy ID, paginating via the
response `Link` header, and writes a TSV with identity fields plus retrieval
provenance (taxon ID/query used, retrieval timestamp, reviewed/unreviewed
counts).

This is a generic, reusable helper -- supply your own --taxon-id for your
organism of interest. Do not hard-code or assume a taxon ID from another
project's dataset.

Example:
    python uniprot_taxon_lookup.py --taxon-id 9606 --out human_accessions.tsv
"""

from __future__ import annotations

import argparse
import csv
import datetime
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"
USER_AGENT = "alphafold-structure-provenance-skill/1.0 (contact: discovery-workspace)"
LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')

FIELDS = "accession,reviewed,protein_name,gene_names,length,cc_subunit"


def fetch_page(url: str, timeout: float = 30.0) -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), dict(resp.headers)


def next_link(headers: dict[str, str]) -> str | None:
    link_header = headers.get("Link") or headers.get("link")
    if not link_header:
        return None
    m = LINK_NEXT_RE.search(link_header)
    return m.group(1) if m else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--taxon-id", required=True, help="NCBI taxonomy ID, e.g. 9606")
    ap.add_argument("--reviewed-only", action="store_true",
                     help="restrict to Swiss-Prot reviewed entries only")
    ap.add_argument("--out", default="uniprot_taxon_accessions.tsv")
    ap.add_argument("--page-size", type=int, default=500)
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    query = f"taxonomy_id:{args.taxon_id}"
    if args.reviewed_only:
        query += " AND reviewed:true"

    url = (
        f"{SEARCH_URL}?query={urllib.parse.quote(query)}"
        f"&fields={FIELDS}&format=tsv&size={args.page_size}"
    )

    retrieved_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    all_lines: list[str] = []
    header: str | None = None
    page = 0

    while url:
        page += 1
        try:
            raw, headers = fetch_page(url)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"UniProt query failed (HTTP {exc.code}) for {url}: {exc.reason}")
        text = raw.decode("utf-8")
        lines = text.strip("\n").split("\n")
        if not lines or not lines[0]:
            break
        if header is None:
            header = lines[0]
            all_lines.extend(lines[1:])
        else:
            all_lines.extend(lines[1:])
        print(f"  page {page}: {len(lines) - 1} rows")
        url = next_link(headers)
        if url:
            time.sleep(args.sleep)

    if header is None:
        raise SystemExit("No results returned -- check the taxon ID / query.")

    out_path = Path(args.out)
    with out_path.open("w", newline="") as fh:
        fh.write(header + "\n")
        for line in all_lines:
            fh.write(line + "\n")

    n_reviewed = sum(1 for line in all_lines if "\ttrue\t" in ("\t" + line + "\t"))
    print(f"Query: {query}")
    print(f"Retrieved at: {retrieved_at}")
    print(f"Total accessions: {len(all_lines)} (approx reviewed count: {n_reviewed})")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
