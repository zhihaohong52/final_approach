"""The anthropic SDK, mcp and langgraph load on first use, not when backend.server is imported (memory on a 512 MB host)."""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "ATM_STORE": "files", "ATM_WARM": "0", "PYTHONPATH": str(ROOT)}


def _run(code: str) -> str:
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout.strip().splitlines()[-1]


def test_server_import_does_not_load_anthropic_mcp_or_langgraph():
    out = _run("import sys, backend.server; print(sorted(m for m in ('anthropic', 'mcp', 'langgraph') if m in sys.modules))")
    assert out == "[]"


def test_server_import_sets_up_root_logging_as_fastmcp_did():
    """main imported mcp_server at start, whose FastMCP(...) ran basicConfig(INFO, RichHandler); the server keeps that."""
    code = """
import logging, sys, backend.server
r = logging.getLogger()
print(r.level, [type(h).__name__ for h in r.handlers], r.handlers[0].formatter._fmt, 'mcp' in sys.modules)
"""
    assert _run(code) == "20 ['RichHandler'] %(message)s False"


def test_first_mcp_request_loads_the_mcp_app():
    code = """
import sys
from fastapi.testclient import TestClient
import backend.server as s
assert 'mcp' not in sys.modules
c = TestClient(s.app, base_url="http://127.0.0.1:8000")   # FastMCP's DNS-rebinding guard allows localhost hosts only
r1 = c.post("/mcp/messages/", json={})
r2 = c.post("/mcp/messages/?session_id=00000000000000000000000000000000", json={})
print(r1.status_code, r1.text, '|', r2.status_code, r2.text, '|', 'mcp' in sys.modules)
"""
    assert _run(code) == "400 session_id is required | 404 Could not find session | True"


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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_mcp_sse_session_over_real_server():
    """A full MCP handshake (SSE stream, initialize, list_tools) against uvicorn: the lazily mounted app serves
    /mcp/sse and the /mcp/messages/ endpoint it advertises."""
    pytest.importorskip("uvicorn")
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.server:app", "--host", "127.0.0.1",
                             "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=ENV,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 180
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert proc.poll() is None, proc.stderr.read().decode()[-2000:]
                assert time.monotonic() < deadline, "uvicorn did not start"
                time.sleep(0.2)

        async def session() -> set[str]:
            async with sse_client(f"http://127.0.0.1:{port}/mcp/sse", timeout=60, sse_read_timeout=120) as (r, w):
                async with ClientSession(r, w) as s:
                    await asyncio.wait_for(s.initialize(), 120)
                    return {t.name for t in (await asyncio.wait_for(s.list_tools(), 120)).tools}

        names = asyncio.run(session())
        assert {"list_waves", "get_wave", "fcfs_plan", "legal_moves", "check_plan", "negotiate"} <= names
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
