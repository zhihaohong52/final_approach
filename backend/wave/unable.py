"""Pilot UNABLE: a pilot refuses a plan by citing a pilot-side rule it fails. Pure formula, no model call.

The spec says "source = PILOT"; in code `source` is a citation string and `owner` is the role, so a
pilot-side check is `owner == "PILOT"` (PLT-EARLIEST-ETA, PLT-FUEL-RESERVE, PLT-CREW-DUTY) plus the
wind limits (ATC-WIND-LIMITS, owned by ATC but a limit of the pilot's own aircraft). There is no
runway-length rule, so nothing is refused on that. The deterministic rule engine still decides
legality; this only words the refusal.

Checks and moves are accepted as objects or as the dicts graph.py stores for rejected moves.
"""
from __future__ import annotations

PILOT_SIDE_ATC_RULES = ("ATC-WIND-LIMITS",)


def _get(x, key, default=None):
    return x.get(key, default) if isinstance(x, dict) else getattr(x, key, default)


def pilot_side(c) -> bool:
    if _get(c, "passed", True) or not _get(c, "callsign"):
        return False
    return _get(c, "owner") == "PILOT" or _get(c, "rule") in PILOT_SIDE_ATC_RULES


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def evidence_words(c) -> str:
    ev = _get(c, "evidence") or {}
    rule = _get(c, "rule")
    fallback = (_get(c, "detail") or "")[:120]
    if rule == "PLT-FUEL-RESERVE" and _num(ev.get("need_min")) and _num(ev.get("extra_min")):
        return f"needs {ev['need_min']:g} min (hold + route) > {ev['extra_min']:g} min extra fuel"
    if rule == "PLT-EARLIEST-ETA" and _num(ev.get("slot_min")) and _num(ev.get("earliest_min")) \
            and ev["slot_min"] < ev["earliest_min"]:
        return f"slot T{ev['slot_min']:g} before earliest T{ev['earliest_min']:g}"
    if rule == "ATC-WIND-LIMITS" and all(_num(ev.get(k)) for k in (
            "crosswind_kt", "limit_crosswind_kt", "tailwind_kt", "limit_tailwind_kt")):
        return (f"crosswind {ev['crosswind_kt']:g}/{ev['limit_crosswind_kt']:g} kt, "
                f"tailwind {ev['tailwind_kt']:g}/{ev['limit_tailwind_kt']:g} kt")
    return fallback


def unable_body(turn: int, c, instruction, plan_id: str, proposed_by: str) -> dict:
    mv = instruction.to_dict() if hasattr(instruction, "to_dict") else instruction
    if mv:
        label = mv.get("label") or ""
        what = label[:1].lower() + label[1:]
    elif proposed_by == "ENGINE":
        what = "the opening plan"
    else:
        what = f"{proposed_by}'s plan"
    rule = _get(c, "rule")
    return {"type": "UNABLE", "turn": turn, "agent": f"PILOT:{_get(c, 'callsign')}",
            "callsign": _get(c, "callsign"), "rule": rule, "source": _get(c, "source"),
            "evidence": dict(_get(c, "evidence") or {}), "detail": _get(c, "detail"),
            "instruction": mv or None, "plan_id": plan_id, "proposed_by": proposed_by,
            "text": f"UNABLE {what}: {evidence_words(c)}, {rule}"}
