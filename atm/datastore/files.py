"""File-backed store: the repo's data/ and eval/ files. Reads and writes land in later tasks."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .base import StoreStatus

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class FilePaths:
    root: Path = ROOT
    runs_dir: Path = ROOT / "eval/runs"
    decisions_path: Path = ROOT / "eval/decisions.jsonl"
    replay_dir: Path = ROOT / "data/replay"
    benchmark_path: Path = ROOT / "eval/benchmark.json"
    iem_dir: Path = ROOT / "data/challenge2/metar_cache"


class FileStore:
    def __init__(self, paths: FilePaths | None = None):
        self.paths = paths or FilePaths()

    def status(self) -> StoreStatus:
        return StoreStatus("files", False, None, False)
