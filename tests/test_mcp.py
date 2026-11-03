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


def test_mcp_plans_are_bounded_like_the_api(monkeypatch):
    """MCP plans go through edit.plan_from_dicts(rows, wave): a huge JSON int, an unknown callsign or a slot
    outside the wave's bounds is a ValueError (an MCP tool error), never an OverflowError or a silent clamp."""
    import pytest
    w = fixtures.make_wave()
    monkeypatch.setattr(build, "build_wave", lambda wave_id: w)
    rows = moves.fcfs_plan(w).to_dict()["assignments"]
    for patch in ({"slot_min": 10 ** 400}, {"slot_min": 1e8}, {"callsign": "ZZ9999"}, {"ground_delay_min": -1}):
        bad = [{**rows[0], **patch}] + rows[1:]
        with pytest.raises(ValueError):
            asyncio.run(mcp_server.get_trajectory(w.id, bad))
        with pytest.raises(ValueError):
            asyncio.run(mcp_server.check_plan(w.id, {"assignments": bad}))
        with pytest.raises(ValueError):
            asyncio.run(mcp_server.legal_moves(w.id, "ATC", {"assignments": bad}))
    assert "feasible" in asyncio.run(mcp_server.check_plan(w.id, {"assignments": rows}))
