#!/usr/bin/env python3
"""Validate only artifacts recorded by this rebuild invocation."""
import argparse
import csv
import gzip
import hashlib
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
args = parser.parse_args()
with (args.directory / "artifacts.tsv").open() as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
if not rows:
    raise SystemExit("No images built in this invocation")
seen = set()
for row in rows:
    name = row["file"]
    if Path(name).name != name or name in seen or not name.endswith(".img.gz"):
        raise SystemExit(f"Invalid artifact name: {name}")
    seen.add(name)
    identity = f'_{row["board"]}_{row["release"]}_{row["kernel"]}_{row["edition"]}_'
    if identity not in name or row["edition"] not in ("server", "desktop"):
        raise SystemExit(f"Artifact identity mismatch: {name}")
    file = args.directory / name
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != row["sha256"]:
        raise SystemExit(f"Artifact checksum mismatch: {name}")
    size = 0
    with gzip.open(file, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
    if not size:
        raise SystemExit(f"Empty image: {name}")
    print(f'{row["board"]} / {row["kernel"]} / {row["edition"]}: {name} ({size} bytes)')
