"""MCP tool tests: in-process, fixture-backed, no network."""
import asyncio

from backend import mcp_server
from backend.wave import build, fixtures, moves


def test_tools_registered():
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert names == {"list_waves", "get_wave", "fcfs_plan", "legal_moves", "check_plan", "negotiate", "get_trajectory"}


def test_check_plan_tool_uses_engine(monkeypatch):
    w = fixtures.make_wave()
    monkeypatch.setattr(build, "build_wave", lambda wave_id: w)
    res = asyncio.run(mcp_server.check_plan(w.id, moves.fcfs_plan(w).to_dict()))   # tools are async (thread-offloaded)
    assert "feasible" in res


def test_get_trajectory_tool(monkeypatch):
    w = fixtures.make_wave()
    monkeypatch.setattr(build, "build_wave", lambda wave_id: w)
    res = asyncio.run(mcp_server.get_trajectory(w.id, moves.fcfs_plan(w).to_dict()["assignments"]))
    assert isinstance(res, dict) and res
