"""Helpers shared by the seeder and the packers: content hashing, deterministic gzip CSV, grouping, LIKE escaping."""
from __future__ import annotations

import gzip
import hashlib
import io
import re
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


# ------------------------------------------------------------------ raw tables: identifiers and type inference

def pg_ident(header: str, taken: set[str] | None = None) -> str:
    """A column name from a file header: lower-case, each run of non-[a-z0-9] -> one `_`, a leading digit gets `c_`,
    cut to 63 bytes. With `taken` (names already used, including reserved ones) a repeat becomes name_2, name_3, ...
    and the result is added to `taken`."""
    name = re.sub(r"[^a-z0-9]+", "_", header.lower()) or "c_"
    if name[0].isdigit():
        name = "c_" + name
    name = name[:63]
    if taken is not None:
        base, k = name, 1
        while name in taken:
            k += 1
            name = base[:63 - len(str(k)) - 1] + f"_{k}"
        taken.add(name)
    return name


_NUM = re.compile(r"-?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
_INTV = re.compile(r"-?\d+(\.0*)?")
_LEAD0 = re.compile(r"-?0\d")        # "007": numeric parsing would drop the zeros, so it stays text
_INT_RANGES = (("int2", -2**15, 2**15 - 1), ("int4", -2**31, 2**31 - 1), ("int8", -2**63, 2**63 - 1))


class TypeScan:
    """Streaming column-type inference over string frames (blank = null). Feed every chunk to `update`, then `types()`.
    A column is numeric only if every non-blank value is a plain number (no sign `+`, spaces, leading zeros, nan/inf)."""

    def __init__(self, columns: list[str]):
        self.columns = list(columns)
        self._seen = dict.fromkeys(columns, False)
        self._num = dict.fromkeys(columns, True)
        self._int = dict.fromkeys(columns, True)
        self._lo = dict.fromkeys(columns, 0)
        self._hi = dict.fromkeys(columns, 0)

    def update(self, df: pd.DataFrame) -> None:
        for i, c in enumerate(self.columns):
            s = df.iloc[:, i]
            s = s[s != ""]
            if s.empty:
                continue
            self._seen[c] = True
            if not self._num[c]:
                continue
            if not s.str.fullmatch(_NUM).all() or s.str.match(_LEAD0).any():
                self._num[c] = False
                continue
            if not self._int[c]:
                continue
            if not s.str.fullmatch(_INTV).all():
                self._int[c] = False
                continue
            ints = s.str.replace(r"\.0*$", "", regex=True)
            try:
                vals = ints.astype("int64")
            except (ValueError, OverflowError):
                self._num[c] = False      # beyond int8: keep it exact as text
                continue
            self._lo[c] = min(self._lo[c], int(vals.min()))
            self._hi[c] = max(self._hi[c], int(vals.max()))

    def types(self) -> dict[str, str]:
        out = {}
        for c in self.columns:
            if not self._seen[c] or not self._num[c]:
                out[c] = "text"
            elif self._int[c]:
                out[c] = next(t for t, lo, hi in _INT_RANGES if lo <= self._lo[c] and self._hi[c] <= hi)
            else:
                out[c] = "float8"
        return out


def infer_pg_types(df: pd.DataFrame) -> dict[str, str]:
    """Postgres type per column: integer-valued numbers -> narrowest of int2/int4/int8, other numbers -> float8,
    all-null and everything else -> text."""
    strs = df.astype(object).where(df.notna(), "").astype(str)
    scan = TypeScan(list(df.columns))
    scan.update(strs)
    return scan.types()
