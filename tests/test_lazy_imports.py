"""The anthropic SDK and langgraph load on first use, not when backend.server is imported (memory on a 512 MB host)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "ATM_STORE": "files", "ATM_WARM": "0", "PYTHONPATH": str(ROOT)}


def _run(code: str) -> str:
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout.strip().splitlines()[-1]


def test_server_import_does_not_load_anthropic_or_langgraph():
    out = _run("import sys, backend.server; print(sorted(m for m in ('anthropic', 'mcp', 'langgraph') if m in sys.modules))")
    assert out == "[]"


def test_server_import_sets_up_root_logging():
    """INFO level, one handler (Rich when installed), as the server had when FastMCP configured it."""
    code = """
import logging, backend.server
r = logging.getLogger()
print(r.level, len(r.handlers), type(r.handlers[0]).__name__ in ('RichHandler', 'StreamHandler'), r.handlers[0].formatter._fmt)
"""
    assert _run(code) == "20 1 True %(message)s"


def test_mcp_is_gone():
    from fastapi.testclient import TestClient
    from backend import server
    assert TestClient(server.app).get("/mcp/sse").status_code == 404


def test_first_negotiation_loads_langgraph():
    code = """
import sys
from backend.wave import graph
from tests.fixtures import make_wave
assert 'langgraph' not in sys.modules
res = graph.negotiate_wave(make_wave(), "formulas")
print('langgraph' in sys.modules, 'anthropic' in sys.modules, bool(res.get('outcome') or res))
"""
    assert _run(code) == "True False True"
