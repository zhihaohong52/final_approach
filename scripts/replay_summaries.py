"""Write data/replay_summaries.json: the WaveSummary of every checked-in replay (data/replay/*.json), read by
server._replay_summaries so the demo list never builds a wave at runtime. Rerun after adding or re-recording a
replay, or after a change to how waves are built; tests/test_replay_summaries.py fails while the file is stale.

    ATM_STORE=files python -m scripts.replay_summaries
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("ATM_STORE", "files")
os.environ.setdefault("ATM_WARM", "0")

from backend import server  # noqa: E402
from backend.datastore import get_store  # noqa: E402


def compute() -> dict[str, dict]:
    with get_store().session():
        return {wid: server._build_replay_summary(wid) for wid in sorted(get_store().replay_ids())}


def main() -> None:
    out = compute()
    server.REPLAY_SUMMARIES_FILE.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"{len(out)} summaries -> {server.REPLAY_SUMMARIES_FILE.relative_to(server.ROOT)}")


if __name__ == "__main__":
    main()
