"""Read-only MCP server exposing wave-mode tools. Mounted under /mcp by backend.server.

Legality is decided only by the deterministic rule engine (backend.wave.rules via utility.evaluate);
nothing here calls a paid model. Run standalone over stdio with `python -m backend.mcp_server`.

Every tool is `async` and runs its body in a worker thread: FastMCP calls a sync tool directly on the event loop, and
the bodies read the datastore (Postgres in production), which would stall every other request of the app."""
from __future__ import annotations

import asyncio
from mcp.server.fastmcp import FastMCP

from backend.wave import build, edit, graph, moves, trajectory, utility
from backend.wave.model import JointPlan

mcp = FastMCP("disruption-negotiator", stateless_http=True)


def _plan_from_dict(d: dict, wave) -> JointPlan:
    """Rebuild a JointPlan from its contract dict for `wave`; metrics are recomputed by the engine, so they are
    dropped. Bounded exactly like the HTTP API (`edit.plan_from_dicts(rows, wave)`): a malformed or out-of-range
    row raises ValueError, which the client sees as a tool error."""
    rows = d.get("assignments", []) if isinstance(d, dict) else None
    if not isinstance(rows, list):
        raise ValueError("plan.assignments must be a list")
    label = d.get("label")
    return JointPlan(edit.plan_from_dicts(rows, wave).assignments, label[:200] if isinstance(label, str) else "")


@mcp.tool()
async def list_waves(airport: str | None = None, q: str | None = None, limit: int = 20) -> list[dict]:
    """List disrupted arrival waves (summaries), optionally filtered by airport code or a flight/wave id
    substring. Read-only. Legality of any plan is decided only by the deterministic rule engine."""
    return await asyncio.to_thread(lambda: build.list_waves(airport=airport, q=q, limit=max(0, min(limit, 500))))


@mcp.tool()
async def get_wave(wave_id: str) -> dict:
    """Return the full wave scenario (flights, runways, weather, gates, queue) for one wave id.
    Read-only. Legality of any plan is decided only by the deterministic rule engine."""
    return await asyncio.to_thread(lambda: build.build_wave(wave_id).to_dict())


@mcp.tool()
async def fcfs_plan(wave_id: str) -> dict:
    """Return the first-come-first-served baseline plan for a wave. Read-only. Legality of the plan is
    decided only by the deterministic rule engine (see check_plan)."""
    return await asyncio.to_thread(lambda: moves.fcfs_plan(build.build_wave(wave_id)).to_dict())


@mcp.tool()
async def legal_moves(wave_id: str, agent_id: str, plan: dict | None = None) -> list[dict]:
    """List the moves an agent (ATC, AIRLINE:<code>, GROUND or PILOT:<cs>) may make from a plan, most useful first.
    AIRLINE:<code> lists only that airline's own-flight moves; PILOT:<cs> lists the per-flight action space the
    fallback solver searches (pilots never propose in a negotiation). The plan defaults to the FCFS plan. Read-only. Legality is decided only by the deterministic rule engine:
    infeasible moves are returned ranked lower so the engine's rejection is visible."""
    def run() -> list[dict]:
        w = build.build_wave(wave_id)
        p = _plan_from_dict(plan, w) if plan is not None else moves.fcfs_plan(w)
        return [m.to_dict() for m in moves.legal_moves(w, p, agent_id)]
    return await asyncio.to_thread(run)


@mcp.tool()
async def check_plan(wave_id: str, plan: dict) -> dict:
    """Evaluate a joint plan for a wave and return its PlanEvaluation (feasibility, violations, utilities).
    Read-only. Legality is decided only by the deterministic rule engine, never by the caller or a model."""
    def run() -> dict:
        w = build.build_wave(wave_id)
        return utility.evaluate(w, _plan_from_dict(plan, w))
    return await asyncio.to_thread(run)


@mcp.tool()
async def get_trajectory(wave_id: str, assignments: list[dict]) -> dict:
    """Return the 4D trajectories (airborne tracks with holding laps and stack levels, ground segments, conflicts)
    of a plan's assignments for a wave. Read-only. Legality is decided only by the deterministic rule engine."""
    def run() -> dict:
        w = build.build_wave(wave_id)
        return trajectory.build(w, _plan_from_dict({"assignments": assignments}, w)).to_dict()
    return await asyncio.to_thread(run)


@mcp.tool()
async def negotiate(wave_id: str) -> dict:
    """Run the formulas-only negotiation for a wave (no paid model calls) and return the result without its
    message log. `agents` are the voters (ATC, one AIRLINE:<code> per airline, GROUND); `pilots` lists the
    PILOT:<cs> agents, which only say UNABLE; `airlines` is the grouping. Read-only. Legality is decided only by the deterministic rule engine."""
    def run() -> dict:
        res = graph.negotiate_wave(build.build_wave(wave_id), "formulas")
        res.pop("log", None)
        return res
    return await asyncio.to_thread(run)


# Mounted at /mcp by backend.server. The SSE transport needs no lifespan hook (streamable HTTP would need
# mcp.session_manager.run() started in the parent app's lifespan).
mcp_asgi_app = mcp.sse_app()


if __name__ == "__main__":
    mcp.run()
