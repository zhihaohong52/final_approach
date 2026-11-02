"""Helpers shared by the seeder and the packers: content hashing, deterministic gzip CSV, grouping, LIKE escaping."""
from __future__ import annotations

import gzip
import hashlib
import io
from collections.abc import Iterator
from pathlib import Path

import pandas as pd


def source_sha256(paths: list[Path], script_version: str) -> str:
    """sha256 over the sorted paths' bytes plus the script version (what `meta.seed_manifest` records)."""
    h = hashlib.sha256()
    h.update(f"v{script_version}\0".encode())
    for p in sorted(paths):
        h.update(p.name.encode() + b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def gzip_csv(df: pd.DataFrame) -> bytes:
    """CSV (no index) gzipped with mtime=0, so the same frame always gives the same bytes."""
    raw = df.to_csv(index=False).encode()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as f:
        f.write(raw)
    return buf.getvalue()


def split_by(df: pd.DataFrame, keys: list[str]) -> Iterator[tuple[tuple, pd.DataFrame]]:
    """Yield (key tuple, sub-frame) per group, in sorted key order."""
    for key, sub in df.groupby(keys, sort=True, observed=True):
        yield (key if isinstance(key, tuple) else (key,)), sub


def like_escape(q: str) -> str:
    """Escape `\\`, `%` and `_` for use in a LIKE pattern."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
