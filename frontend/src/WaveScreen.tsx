import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isOffline } from "./api/client";
import type { Assignment, Config, EvaluateResponse, FlightRow, JointPlan, Mode, NegotiationMessage, NegotiationResult, PlanEvaluation, TrajectorySet, WaveGeo, WaveScenario, WaveSummary } from "./api/types";
import { icao, icaoText, spoken } from "./callsign";
import { AlertList, RULE_NAME, deriveAlerts } from "./components/Alerts";
import { Gantt, GanttLegend, type DragPreview, type Patch, type Rejection, type View } from "./components/Gantt";
import { Log } from "./components/Log";
import { Checks, Limits, Outcome, PlanTable, Sources, Weather } from "./components/Panels";
import { Radar, SCAN_MIN, type Tone } from "./components/Radar";
import { Strips } from "./components/Strips";
import { Surface, type RejectedGround } from "./components/Surface";
import { agentName, clock, clockSec, dateLabel, signed } from "./format";
import { effectiveChoice, shownPanel as pickPanel, type Panel } from "./panel";
import { vetoConflicts } from "./veto";

const SCAN_S = SCAN_MIN * 60;       // the radar picture updates once per antenna revolution
const BOTH_MQ = "(min-width: 1280px)";   // radar and surface side by side from this width
const SPEEDS = [1, 10, 60];

const UNDO_KEY = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘Z" : "Ctrl+Z";

type Status = "idle" | "running" | "done" | "error";
type Decision = { kind: "APPROVE" | "OVERRIDE"; at: string; note?: string; offline: boolean; edits: number } | null;
const MODE_LABEL: Record<Mode, string> = { formulas: "Formulas", decision: "Decision models", "decision+claude": "Decision models + Claude brief" };
type Tab = "rules" | "data" | "limits" | "sources";
const TABS: { id: Tab; label: string }[] = [
  { id: "rules", label: "Safety rules" }, { id: "data", label: "Weather and wind" }, { id: "limits", label: "Limits used" }, { id: "sources", label: "Data sources" },
];
type Edits = Record<string, Patch>;

function applyEdits(p: JointPlan, edits: Edits, checked: EvaluateResponse | null): JointPlan {
  if (!Object.keys(edits).length) return p;
  const fromServer = new Map((checked?.plan.assignments ?? []).map((a) => [a.callsign, a]));
  return {
    ...p, id: checked?.plan.id ?? `${p.id}-edited`, label: "edited by the duty manager",
    assignments: p.assignments.map((a) => {
      const e = edits[a.callsign];
      const srv = fromServer.get(a.callsign);
      const next: Assignment = e ? { ...a, ...e } : a;
      // metrics come from the server's re-check when it has seen this exact assignment; until then shift in-block with the slot
      if (srv && srv.runway === next.runway && srv.slot_min === next.slot_min && srv.gate === next.gate) return { ...next, metrics: srv.metrics };
      if (e?.slot_min != null && a.slot_min != null) return { ...next, metrics: { ...a.metrics, in_block_min: (a.metrics.in_block_min ?? a.slot_min) + (e.slot_min - a.slot_min) } };
      return next;
    }),
  };
}

// what makes two plans fly the same paths: the trajectory request is re-sent only when this changes
const planKey = (p: JointPlan | null) => (p ? JSON.stringify(p.assignments.map((a) => [a.callsign, a.action, a.runway ?? null, a.slot_min ?? null, a.gate ?? null, a.speed_reduction_min, a.ground_delay_min])) : "");
type Fetched = { key: string; set: TrajectorySet | null };

export function WaveScreen({ onScope }: { onScope?: (s: string) => void }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [waves, setWaves] = useState<WaveSummary[]>([]);
  const [wave, setWave] = useState<WaveScenario | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("decision");
  const [model, setModel] = useState("");
  const [replay, setReplay] = useState(true);

  const [status, setStatus] = useState<Status>("idle");
  const [messages, setMessages] = useState<NegotiationMessage[]>([]);
  const [table, setTable] = useState<JointPlan | null>(null);
  const [rejection, setRejection] = useState<Rejection | null>(null);
  // the plan a rejected move was tried on (the table before that turn), and the ground conflicts the engine finds
  // when it applies the move there: the surface's counterpart of the timeline's red rejection flash
  const rejectBase = useRef<Assignment[] | null>(null);
  const [rejectGround, setRejectGround] = useState<RejectedGround | null>(null);
  const [result, setResult] = useState<NegotiationResult | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [view, setView] = useState<View>("after");
  const [decision, setDecision] = useState<Decision>(null);
  const [overriding, setOverriding] = useState(false);
  const [note, setNote] = useState("");
  const [deciding, setDeciding] = useState(false);
  const [tab, setTab] = useState<Tab>("rules");
  const [opening, setOpening] = useState<{ plan: JointPlan; evaluation: PlanEvaluation } | null>(null);
  const [details, setDetails] = useState(false);
  // the duty manager's edits, their undo history, and the server's re-check of the edited plan
  const [edits, setEdits] = useState<Edits>({});
  const [undo, setUndo] = useState<Edits[]>([]);
  const [checked, setChecked] = useState<EvaluateResponse | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkErr, setCheckErr] = useState<string | null>(null);
  const [seen, setSeen] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [t, setT] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(10);          // replay speed: replayed seconds per real second
  const [geo, setGeo] = useState<WaveGeo | null>(null);           // procedures for the wave; null = the radar stays schematic
  const [fetched, setFetched] = useState<Fetched | null>(null);   // the engine's paths for a plan the run did not embed
  const [pathsBusy, setPathsBusy] = useState(false);
  const [panel, setPanel] = useState<Panel | null>(null);        // the duty manager's pick of picture; null = the default (see panel.ts)
  const [wideScreen, setWideScreen] = useState(() => typeof matchMedia !== "undefined" && matchMedia(BOTH_MQ).matches);
  useEffect(() => {
    const mq = matchMedia(BOTH_MQ);
    const on = () => setWideScreen(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  // a bar being dragged on the timeline, and the server's re-check of where it is now (hold laps and taxi included)
  const [dragAt, setDragAt] = useState<{ cs: string; patch: Patch } | null>(null);
  const [preview, setPreview] = useState<DragPreview | null>(null);
  const [previewErr, setPreviewErr] = useState(false);
  const lastPreview = useRef<{ key: string; resp: EvaluateResponse } | null>(null);   // the latest drag re-check, reused when the drop is that plan
  const abort = useRef<AbortController | null>(null);
  const tableRef = useRef<JointPlan | null>(null);
  const showTable = (p: JointPlan | null) => { tableRef.current = p; setTable(p); };
  useEffect(() => () => abort.current?.abort(), []);

  const resetEdits = () => { lastPreview.current = null; setEdits({}); setUndo([]); setChecked(null); setCheckErr(null); setSeen(new Set()); };
  const loadSeq = useRef(0);
  const loadWave = useCallback(async (id: string) => {
    const seq = ++loadSeq.current;          // a newer load wins; an older one that resolves late is dropped
    abort.current?.abort();
    setStatus("idle"); setMessages([]); showTable(null); setResult(null); setRejection(null); setRunErr(null); setOpening(null);
    setDecision(null); setOverriding(false); setNote(""); setView("after"); setSelected(null); setT(null); setPlaying(false); resetEdits();
    setGeo(null); setFetched(null);
    try {
      const w = await api.wave(id);
      if (seq !== loadSeq.current) return;
      setWave(w); setLoadErr(null);
      // procedures are only served for waves the backend can place; a failure leaves the schematic radar
      if (w.geo_available) api.getGeo(id).then((g) => { if (seq === loadSeq.current) setGeo(g); }).catch((e) => console.warn(`wave ${id}: procedures unavailable, the radar stays schematic`, e));
      // the status quo is on screen from the start; the negotiation then works from it
      if (w.status_quo) { showTable(w.status_quo.plan); setOpening(w.status_quo); } history.replaceState(null, "", `${location.search}#/wave/${id}`);
    } catch (e) { if (seq === loadSeq.current) setLoadErr(`Couldn't load wave ${id}: ${(e as Error).message}`); }
  }, []);

  useEffect(() => {
    let gone = false;
    (async () => {
      try {
        const [c, ws] = await Promise.all([api.config(), api.waves()]);
        if (gone) return;
        setConfig(c); setMode(c.default_mode === "decision+claude" ? "decision" : c.default_mode); setModel(c.default_model); setReplay(c.replay_available); setWaves(ws);
        const fromHash = location.hash.match(/#\/wave\/(.+)/)?.[1];
        const first = fromHash ?? ws[0]?.id ?? (await api.randomWave()).id;
        await loadWave(decodeURIComponent(first));
      } catch (e) { if (!gone) setLoadErr(`Couldn't reach the negotiator: ${(e as Error).message}`); }
    })();
    return () => { gone = true; };
  }, [loadWave]);

  const negotiate = async () => {
    if (!wave) return;
    abort.current?.abort();
    const ctl = new AbortController(); abort.current = ctl;
    setStatus("running"); setMessages([]); showTable(wave.status_quo?.plan ?? null); setResult(null); setRejection(null); setRunErr(null); setDecision(null); setView("after"); setOpening(wave.status_quo ?? null); resetEdits();
    let ended = false;
    try {
      await api.negotiate({ wave_id: wave.id, mode, model: mode === "formulas" ? undefined : model, replay }, (ev) => {
        if (ev.event === "message") {
          const m = ev.data;
          setMessages((xs) => [...xs, m]);
          const b = m.body;
          if (b.type === "AGENT_TURN") {
            const r = b.rejected[b.rejected.length - 1];
            if (r) {
              const cs = r.move.callsigns[0];
              rejectBase.current = tableRef.current?.assignments ?? null;
              setRejection({ seq: m.seq, move: r.move, violations: r.violations, runway: tableRef.current?.assignments.find((a) => a.callsign === cs)?.runway });
            }
            showTable(b.proposal);
          }
          if (b.type === "OPEN") setOpening({ plan: b.plan, evaluation: b.evaluation });
          if (b.type === "OPEN" || b.type === "MOVE" || b.type === "COUNTER") showTable(b.plan);
          if (b.type === "MOVE_REJECTED") {
            const cs = b.move.callsigns[0];
            rejectBase.current = tableRef.current?.assignments ?? null;
            setRejection({ seq: m.seq, move: b.move, violations: b.violations, runway: tableRef.current?.assignments.find((a) => a.callsign === cs)?.runway });
          }
        } else if (ev.event === "result") {
          ended = true;
          setResult(ev.data); showTable(ev.data.final_plan); setRejection(null); setStatus("done");
          if (ev.data.log) setMessages(ev.data.log);
        } else if (ev.event === "error") {
          ended = true;
          setRunErr(ev.data.message); setStatus("error");
        }
      }, ctl.signal);
      if (!ended && !ctl.signal.aborted) { setRunErr("The negotiation stream ended before a result arrived."); setStatus("error"); }
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setRunErr((e as Error).message); setStatus("error");
    }
  };

  // ---------- editing ----------
  const nEdits = Object.keys(edits).length;
  const canEdit = status === "done" && !!result && !decision && view !== "before";
  const edit = (cs: string, p: Patch) => {
    if (!result) return;
    const orig = result.final_plan.assignments.find((a) => a.callsign === cs);
    if (!orig) return;
    const merged = { ...edits[cs], ...p };
    const same = (merged.runway ?? orig.runway) === orig.runway && (merged.slot_min ?? orig.slot_min) === orig.slot_min && (merged.gate ?? orig.gate) === orig.gate;
    const next = { ...edits };
    if (same) delete next[cs]; else next[cs] = merged;
    setUndo((u) => [...u.slice(-29), edits]); setEdits(next); setSelected(cs);
  };
  const revert = (cs?: string) => {
    setUndo((u) => [...u.slice(-29), edits]);
    if (!cs) { setEdits({}); return; }
    const next = { ...edits }; delete next[cs]; setEdits(next);
  };
  const undoLast = () => { if (!undo.length) return; setEdits(undo[undo.length - 1]); setUndo((u) => u.slice(0, -1)); };
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !(e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement)) { e.preventDefault(); undoLast(); }
    };
    addEventListener("keydown", on);
    return () => removeEventListener("keydown", on);
  });

  // every change to the edits is re-checked by the server against the same 12 rules
  useEffect(() => {
    if (!result || !wave || !nEdits) { setChecked(null); setChecking(false); setCheckErr(null); return; }
    const ctl = new AbortController();
    const plan = applyEdits(result.final_plan, edits, null);
    const sent = planKey(plan);
    const pre = lastPreview.current;
    if (pre && pre.key === sent) {            // a drop onto the position the drag last checked: that response is this check
      setChecked(pre.resp); if (pre.resp.trajectories) setFetched({ key: sent, set: pre.resp.trajectories }); setChecking(false); setCheckErr(null);
      return;
    }
    setChecking(true); setCheckErr(null);
    const timer = setTimeout(() => {
      // the same response carries the edited plan's paths (include_trajectories); they are kept under the key of the
      // plan that was sent, so they are used only while that is the plan on screen
      api.postEvaluate({ wave_id: wave.id, assignments: plan.assignments }, { includeTrajectories: true, signal: ctl.signal })
        .then((r) => { setChecked(r); if (r?.trajectories) setFetched({ key: sent, set: r.trajectories }); setChecking(false); if (!r) setCheckErr("offline"); })
        .catch((e) => { if ((e as Error).name !== "AbortError") { setCheckErr((e as Error).message); setChecking(false); } });
    }, 120);
    return () => { clearTimeout(timer); ctl.abort(); };
  }, [edits, result, wave]); // eslint-disable-line react-hooks/exhaustive-deps

  const decide = async (kind: "APPROVE" | "OVERRIDE") => {
    if (!result) return;
    setDeciding(true);
    try {
      const plan = applyEdits(result.final_plan, edits, checked);
      const r = await api.decision({ run_id: result.run_id, decision: kind, note: kind === "OVERRIDE" ? note.trim() : undefined,
        edits: nEdits ? plan.assignments.filter((a) => edits[a.callsign]) : undefined });
      setDecision({ kind, at: r.recorded_at, note: note.trim() || undefined, offline: isOffline(), edits: nEdits }); setOverriding(false); setSelected(null);
    } catch (e) { setRunErr(`Decision not recorded: ${(e as Error).message}`); }
    finally { setDeciding(false); }
  };

  const autorun = useRef(new URLSearchParams(location.search).has("autorun"));
  useEffect(() => {
    if (wave && autorun.current) { autorun.current = false; negotiate(); }
  }, [wave]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------- derived ----------
  const agreed = result?.final_plan ?? null;
  const current = agreed ? applyEdits(agreed, edits, checked) : table;
  const currentEv: PlanEvaluation | null = nEdits ? checked?.evaluation ?? null : result?.evaluation ?? (table && table === opening?.plan ? opening.evaluation : null);
  const baseline = result?.baseline.plan ?? opening?.plan ?? null;
  const shown = view === "before" && result ? result.baseline.plan : current;
  // A failing check names its flight and often the other one it conflicts with (wake: "... behind OO5559").
  // When the duty manager's edit is one of them, the breach is theirs: the edited flight keeps its colour with a
  // red outline and the untouched neighbour gets only the outline. Otherwise the failing flight is drawn red.
  const failing = new Map<string, string>();
  const involved = new Set<string>();
  const editsOn = view !== "before";
  for (const c of (view === "before" ? result?.baseline.evaluation : currentEv)?.checks ?? []) {
    if (c.passed || !c.callsign) continue;
    const named = (wave?.flights ?? []).map((f) => f.callsign).filter((cs) => cs === c.callsign || c.detail.includes(cs));
    named.forEach((cs) => involved.add(cs));
    if (!(editsOn && named.some((cs) => edits[cs])) && !failing.has(c.callsign)) failing.set(c.callsign, c.rule);
  }
  const tone = (cs: string): Tone => (failing.has(cs) ? "alert" : editsOn && edits[cs] ? "edited" : cs === wave?.focus ? "focus" : "flight");
  const alerts = useMemo(() => (wave ? deriveAlerts(wave, result, current, nEdits ? currentEv : result?.evaluation ?? null) : []), [wave, result, current, currentEv, nEdits]);
  const red = alerts.filter((a) => a.level === "red");
  const unseen = alerts.filter((a) => a.level === "amber" && !seen.has(a.id));
  const turn = (() => {
    const ev = result?.evaluation ?? opening?.evaluation;
    const v = ev?.checks.find((c) => c.rule === "GND-GATE-FREE" && c.evidence.turnaround_min != null)?.evidence.turnaround_min;
    return v != null ? Number(v) : 45;
  })();
  const blocked = nEdits > 0 && (checking || !!checkErr && checkErr !== "offline" || (checked != null && !checked.evaluation.feasible));

  // while a bar is dragged its hold laps and taxi follow it: the drag position is re-checked with trajectories,
  // debounced, one request in flight (a newer position aborts the older request)
  useEffect(() => {
    if (!dragAt || !result || !wave) { setPreview(null); setPreviewErr(false); return; }
    const ctl = new AbortController();
    const { cs, patch } = dragAt;
    const plan = applyEdits(result.final_plan, { ...edits, [cs]: { ...edits[cs], ...patch } }, null);
    const timer = setTimeout(() => {
      api.postEvaluate({ wave_id: wave.id, assignments: plan.assignments }, { includeTrajectories: true, signal: ctl.signal })
        .then((r) => { setPreviewErr(false); if (r) lastPreview.current = { key: planKey(plan), resp: r }; if (r) setPreview({ cs, patch, metrics: r.plan.assignments.find((a) => a.callsign === cs)?.metrics ?? null, set: r.trajectories ?? null }); })
        .catch((e) => {
          if ((e as Error).name === "AbortError") return;      // a newer drag position replaced this request
          console.warn("drag preview: re-check failed", e);   // the bar keeps its local preview, marked as such
          setPreviewErr(true);
        });
    }, 90);
    return () => { clearTimeout(timer); ctl.abort(); };
  }, [dragAt, result, wave]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------- the engine's paths for the plan on screen ----------
  // A finished run embeds its final and status-quo paths, and a duty manager's edit comes back with its paths from
  // /api/evaluate; the rest (the status quo before a run, the proposals as they stream in) is asked of
  // /api/trajectories. A run recorded on another engine has no paths (`paths_unavailable`; for results saved
  // before that flag, the engine versions are compared), and the radar says so instead of drawing any.
  const recordedOn = result ? result.engine_version ?? "v2" : null;
  // (an edited plan is not the recorded one: its paths come from the current engine, via /api/evaluate)
  const noPaths = !!result && !result.trajectories && !(nEdits > 0 && view !== "before") && (result.paths_unavailable ?? recordedOn !== (wave?.engine_version ?? "v2"));
  const embedded = result?.trajectories ? (view === "before" ? result.trajectories.baseline : !nEdits ? result.trajectories.final : undefined) : undefined;
  const shownKey = planKey(shown);
  const shownRef = useRef(shown); shownRef.current = shown;
  const evalPending = nEdits > 0 && editsOn && checking;         // /api/evaluate is about to bring this plan's paths
  const fetchable = !!geo && !!wave && !!shown && !noPaths && !embedded;
  const wantFetch = fetchable && !evalPending;
  useEffect(() => {
    if (!wantFetch || fetched?.key === shownKey) { setPathsBusy(false); return; }
    const ctl = new AbortController();
    setPathsBusy(true);
    const timer = setTimeout(() => {
      api.postTrajectories(wave!.id, shownRef.current!.assignments, ctl.signal)
        .then((set) => { setFetched({ key: shownKey, set }); setPathsBusy(false); })
        .catch((e) => {
          if ((e as Error).name === "AbortError") return;
          console.warn("paths: request failed", e);
          setFetched({ key: shownKey, set: null }); setPathsBusy(false);
        });
    }, 150);
    return () => { clearTimeout(timer); ctl.abort(); };
  }, [wantFetch, shownKey, wave?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  // a move the engine vetoed for ATC-TAXI-CONFLICT: one /api/trajectories request (the engine applies the move) for
  // the failing ground conflicts behind that veto; the surface shows them briefly, as the timeline flashes the slot.
  // Only a taxi-conflict veto is drawn, and only the conflicts of the flights that veto names: the map never blames
  // the ground for a veto the engine made on another rule.
  // The base is an approximation: the table on screen when the rejection arrived. The engine tried the move on its
  // own working plan, which in turn 1 is that table, but from turn 2 starts from the incumbent (not the last
  // AGENT_TURN proposal shown here), and also includes the moves the agent applied earlier in the same turn.
  const canRejectPaths = !!geo?.surface && !!wave && !noPaths;
  useEffect(() => {
    const base = rejectBase.current;
    if (!rejection || !canRejectPaths || !base || !vetoConflicts(rejection.violations)) { setRejectGround(null); return; }
    const ctl = new AbortController();
    const { seq, move, violations } = rejection;
    api.postTrajectories(wave!.id, base, ctl.signal, move)
      .then((set) => setRejectGround(set ? { seq, label: move.label, rule: RULE_NAME["ATC-TAXI-CONFLICT"], conflicts: vetoConflicts(violations, set.conflicts) ?? [] } : null))
      .catch((e) => { if ((e as Error).name !== "AbortError") { console.warn("rejection paths: request failed", e); setRejectGround(null); } });
    return () => ctl.abort();
  }, [rejection?.seq, canRejectPaths]); // eslint-disable-line react-hooks/exhaustive-deps
  // while a new plan's paths are on their way the previous ones stay up, marked "updating"
  const paths: TrajectorySet | null = !geo || noPaths ? null : embedded ?? (fetchable ? fetched?.set ?? null : null);
  const pathsNote = noPaths ? `recorded on engine ${recordedOn ?? "v2"}; paths not available` : null;
  const updatingPaths = (pathsBusy || evalPending) && !!paths;

  // the replay clock: starts a few minutes before the disrupted flight lands, so it is on the scope
  const focusA = shown?.assignments.find((a) => a.callsign === wave?.focus);
  const defaultT = focusA?.slot_min != null ? Math.round(focusA.slot_min - 2) : 0;   // ~6 NM out on final
  const tNow = t ?? defaultT;
  // the replay runs until the last arrival is on blocks (its taxi is on the surface map), not just down
  const replayEnd = Math.max(defaultT + 1,
    ...(shown?.assignments ?? []).filter((a) => a.action === "land" && a.slot_min != null).map((a) => Math.max(a.slot_min!, a.metrics.in_block_min ?? 0) + 1),
    ...(paths?.ground ?? []).filter((g) => g.wave && g.points.length).map((g) => g.points[g.points.length - 1].t_min + 0.5));
  // The replay clock runs continuously at the chosen speed and ticks in whole seconds. The pictures step, as on a
  // controller's display: the radar once per scan (it floors t to its 4.8 s scan), the surface map once a second.
  useEffect(() => {
    if (!playing) return;
    let raf = 0, last = performance.now(), sim = t ?? defaultT;
    const tick = (now: number) => {
      // a background tab gets no frames; on return, count at most a quarter second so the replay doesn't leap
      sim += (Math.min(now - last, 250) / 1000) * (speed / 60);
      last = now;
      const sec = Math.floor(sim * 60) / 60;
      setT((prev) => (prev === sec ? prev : sec));        // re-render only when a new second lands
      if (sim >= replayEnd) { setPlaying(false); return; }  // the last arrival is down
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- t is read once when play starts; the loop owns it after that
  }, [playing, speed, defaultT, replayEnd]);

  // One panel, two pictures on the same clock: the approach radar until the disrupted flight touches down, the
  // surface map after it (automatic hand-off), or either by hand; side by side on wide screens. Without a
  // surface for the wave (M1, or no OSM data) the radar is the only picture and no toggle is offered.
  const surf = geo?.surface ?? null;
  const focusAir = paths?.air.find((a) => a.callsign === wave?.focus);
  const focusDown = focusA?.action === "land" ? focusA.slot_min ?? focusAir?.points[focusAir.points.length - 1]?.t_min ?? null : null;
  const canBoth = wideScreen && !!surf;
  const choice = effectiveChoice({ choice: panel, surface: surf?.status ?? null, canBoth });
  const shownPanel = pickPanel({ choice: panel, surface: surf?.status ?? null, canBoth, touchdown: focusDown, t: tNow });

  useEffect(() => { if (wave) onScope?.(`${wave.airport.icao} · ${wave.id}`); }, [wave, onScope]);
  const onTabKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = TABS.findIndex((x) => x.id === tab), n = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length].id;
    setTab(n); document.getElementById(`tab-${n}`)?.focus();
  };

  if (!wave) {
    return (
      <main className="canvas">
        <div className="titlerow"><div><h1>Arrival wave</h1><p className="sub">{loadErr ?? "Loading the wave…"}</p></div></div>
        {loadErr && <button className="btn" onClick={() => location.reload()}>Retry</button>}
      </main>
    );
  }
  const lastTurn = [...messages].reverse().find((m) => m.body.type === "AGENT_TURN");
  const turnsDone = result ? (result.turns ?? result.rounds ?? 0) : lastTurn?.round ?? 0;
  const [t0, t1] = [clock(wave.window_start, 0), clock(wave.window_start, 60)];
  const closed = wave.airport.runways.filter((r) => !r.open);
  const m = wave.metar;
  const wind = m ? (m.wind_kt ? `${m.wind_dir_deg != null ? String(Math.round(m.wind_dir_deg)).padStart(3, "0") : "VRB"}° ${m.wind_kt}${m.gust_kt ? `G${m.gust_kt}` : ""} kt` : "wind calm") : "no METAR";
  const radio = lastRadio(messages);
  const focusName = wave.focus ? icao(wave.focus) : null;

  return (
    <main className="canvas ops">
      <header className="wavebar">
        <div className="wb-main">
          <h1>{wave.airport.iata} arrivals <span className="mono">{t0}–{t1}</span>
            {red.length + unseen.length > 0 && <span className={`pill alert-count ${red.length ? "neg" : "warn"}`}>{red.length + unseen.length} alert{red.length + unseen.length === 1 ? "" : "s"}</span>}</h1>
          <p className="wb-meta">
            <span>{dateLabel(wave.window_start)}</span>
            <span className="mono">{wind}{m ? `, ${m.surface}` : ""}</span>
            {closed.length > 0 && <span className="closed-rwy">{closed.map((r) => r.id).join(" ")} closed</span>}
            <span>{wave.flights.length} negotiated · {wave.queue.length} other arrival{wave.queue.length === 1 ? "" : "s"}</span>
          </p>
        </div>
        <div className="toolbar">
          <label className="field"><span className="sr-only">Wave</span>
            <select className="select" value={wave.id} onChange={(e) => loadWave(e.target.value)} aria-label="Wave">
              {waves.map((w) => <option key={w.id} value={w.id}>{w.id} · {w.n_flights} flights</option>)}
              {!waves.some((w) => w.id === wave.id) && <option value={wave.id}>{wave.id}</option>}
            </select>
          </label>
          <FlightPicker airport={wave.airport.iata} onPick={(id) => loadWave(id)} />
          <button className="btn text" onClick={async () => loadWave((await api.randomWave()).id)}>Random</button>
          <details className="settings">
            <summary className="btn text"><span className="lbl">{mode === "formulas" ? "Formulas" : config?.models.find((x) => x.id === model)?.label ?? MODE_LABEL[mode]}{replay ? " · replay" : ""}</span></summary>
            <div className="menu right">
              <div className="field">Decision mode
                <div className="seg" role="group" aria-label="Decision mode">
                  {(config?.modes ?? ["formulas", "decision"]).filter((x) => x !== "decision+claude").map((x) => (
                    <button key={x} aria-pressed={mode === x} onClick={() => setMode(x)}>{MODE_LABEL[x]}</button>
                  ))}
                </div>
              </div>
              {mode !== "formulas" && config && (
                <label className="field">Model
                  <select className="select" value={model} onChange={(e) => setModel(e.target.value)}>
                    {config.models.filter((x) => x.provider !== "formula").map((x) => (
                      <option key={x.id} value={x.id} disabled={!x.available}>{x.label}{x.benchmark_rank === 1 ? " (benchmark winner)" : ""}{x.available ? "" : " (unavailable)"}</option>
                    ))}
                  </select>
                </label>
              )}
              {config?.replay_available && (
                <label className="check"><input type="checkbox" checked={replay} onChange={(e) => setReplay(e.target.checked)} />Replay the recorded run</label>
              )}
            </div>
          </details>
          {isOffline() && <span className="pill" title="The backend is not reachable; this replays the backend's recorded sample run">Offline demo</span>}
        </div>
      </header>

      <div className="ops-top">
        <Strips wave={wave} plan={shown} agreed={agreed} baseline={baseline} edited={(cs) => editsOn && !!edits[cs]} t={tNow}
          tone={tone} selected={selected} onSelect={setSelected} paths={pathsNote ? null : paths} geo={geo} updating={updatingPaths} />
        <section className="scope-card" aria-label={shownPanel === "surface" ? "Airport surface" : shownPanel === "both" ? "Approach radar and airport surface" : "Approach radar"}>
          <div className="scope-head">
            <div className="scope-title">
              {/* with the toggle on screen it names the picture (the one shown is lit), so the heading is for screen readers */}
              <h2 className={surf ? "sr-only" : undefined}>{shownPanel === "surface" ? "Surface" : shownPanel === "both" ? "Approach and surface" : "Approach"}</h2>
              {surf && (
                <div className="seg panel-seg" role="group" aria-label="Picture shown">
                  <button aria-pressed={choice === "auto"} onClick={() => setPanel("auto")}
                    title={`Approach until ${wave.focus ? icao(wave.focus) : "the disrupted flight"} touches down, then the surface`}>Auto</button>
                  <button aria-pressed={choice === "radar"} className={shownPanel !== "surface" ? "showing" : undefined} onClick={() => setPanel("radar")}>Approach</button>
                  <button aria-pressed={choice === "surface"} className={shownPanel !== "radar" ? "showing" : undefined} onClick={() => setPanel("surface")}>Surface</button>
                  {canBoth && <button aria-pressed={choice === "both"} onClick={() => setPanel("both")} title="Approach radar and surface side by side">Both</button>}
                </div>
              )}
            </div>
            <div className="clockctl">
              <button className="icon-btn" onClick={() => { if (!playing && tNow >= replayEnd) setT(defaultT); setPlaying((p) => !p); }} aria-label={playing ? "Pause the replay clock" : "Play the replay clock"} aria-pressed={playing}>
                {playing ? <svg width="12" height="12" aria-hidden="true"><rect x="2" y="1" width="3" height="10" fill="currentColor" /><rect x="7" y="1" width="3" height="10" fill="currentColor" /></svg>
                  : <svg width="12" height="12" aria-hidden="true"><path d="M3 1l8 5-8 5z" fill="currentColor" /></svg>}
              </button>
              <span className="clock mono">{clockSec(wave.window_start, tNow)}</span>
              <div className="speed seg" role="group" aria-label="Replay speed">
                {SPEEDS.map((v) => <button key={v} aria-pressed={speed === v} onClick={() => setSpeed(v)} title={v === 1 ? `Real time: one radar scan every ${SCAN_S} s` : `${v} times real time`}>{v}×</button>)}
              </div>
              {t != null && <button className="btn text sm" onClick={() => { setT(null); setPlaying(false); }}>Reset</button>}
            </div>
          </div>
          <div className={`scope-body ${shownPanel}`}>
            {/* both stay mounted, so the radar keeps its range and the map its zoom across the hand-off */}
            <div className={`pane${shownPanel === "surface" ? " off" : ""}`}>
              <Radar wave={wave} plan={shown} t={tNow} tone={tone} selected={selected} onSelect={setSelected}
                geo={geo} paths={paths} pathsNote={pathsNote} updating={updatingPaths} fill={shownPanel === "both"} />
            </div>
            {surf && (
              <div className={`pane${shownPanel === "radar" ? " off" : ""}`}>
                <Surface geo={geo!} traj={pathsNote ? null : paths} t={tNow} tone={tone} selected={selected} onSelect={setSelected}
                  focus={wave.focus} updating={updatingPaths} active={shownPanel !== "radar"} rejected={view !== "before" ? rejectGround : null} />
              </div>
            )}
          </div>
          <div className="scope-key" aria-hidden="true">
            {wave.focus && <span><i className="k k-focus" />Disrupted</span>}
            <span><i className="k k-flight" />Re-planned</span>
            {nEdits > 0 && <span><i className="k k-edited" />Your edit</span>}
            {red.length > 0 && <span><i className="k k-alert" />Breaks a rule</span>}
            {shownPanel !== "surface" && <span><i className="k k-fixed" />Other traffic</span>}
            {shownPanel !== "radar" && <span><i className="k k-ground" />{shownPanel === "both" ? "Ground traffic" : "Other traffic, departures"}</span>}
          </div>
        </section>

        <aside className="decision" aria-label="Decision">
          {alerts.length > 0 && <AlertList alerts={alerts} seen={seen} onSeen={(id) => setSeen((s) => new Set(s).add(id))} onFocus={(cs) => setSelected(cs)} />}

          {status === "idle" || status === "error" ? (
            <div className="dec-intro">
              <p className="dec-kicker">Disrupted flight</p>
              <p className="dec-head">{focusName ? <>{focusName} <span className="muted">{spoken(wave.focus!)}</span></> : "No single focus flight"}</p>
              <p className="dec-line">{intro(wave)}</p>
              <p className="dec-line muted">ATC, the pilot and Ground negotiate its runway, slot and gate for up to 3 turns. A rule engine vetoes any plan that breaks one of 12 safety rules. You approve, edit or override the result.</p>
              {runErr && <p className="dec-line neg">{runErr}</p>}
              <div className="dec-actions"><button className="btn primary wide" onClick={negotiate}>{status === "error" ? "Run again" : "Start negotiation"}</button></div>
            </div>
          ) : status === "running" ? (
            <div className="dec-intro">
              <p className="dec-kicker">Negotiating · turn {Math.max(1, lastTurn?.round ?? 1)} of 3</p>
              <div className="turns" aria-hidden="true">{[1, 2, 3].map((i) => <span key={i} className={i < turnsDone ? "on" : i === Math.max(1, turnsDone) ? "now" : ""} />)}</div>
              <p className="dec-line">{radio ? <><b className={`who-inline ${radio.cls}`}>{radio.who}</b> {radio.text}</> : "Triage and the opening plan…"}</p>
              {rejection && <p className="dec-line neg">Vetoed: {icaoText(rejection.move.label)}. {icaoText(rejection.violations[0]?.detail ?? "")}</p>}
              <div className="dec-actions"><button className="btn wide" disabled>Approve once the agents agree</button></div>
            </div>
          ) : result && (
            <div className="dec-result">
              <p className="dec-kicker">{result.outcome === "consensus" ? `Agents agreed in turn ${turnsDone} of 3` : "No agreement · solver decided"}{nEdits ? ` · ${nEdits} edit${nEdits === 1 ? "" : "s"} by you` : ""}</p>
              <Revision r={result} plan={current} wave={wave} />
              <Kpis ev={currentEv ?? (nEdits ? null : result.evaluation)} base={result.baseline.evaluation} pending={nEdits > 0 && checking} />

              {nEdits > 0 && (
                <div className={`edit-card${checked && !checked.evaluation.feasible ? " bad" : ""}`}>
                  <div className="ec-head">
                    <b>Your edits</b>
                    <span className={checking ? "muted" : checkErr === "offline" ? "muted" : checkErr ? "breach" : checked?.evaluation.feasible ? "pos" : "breach"}>
                      {checking ? "re-checking…" : checkErr === "offline" ? "can't re-check offline" : checkErr ? "re-check failed" : checked?.evaluation.feasible ? `all ${new Set(checked.evaluation.checks.map((c) => c.rule)).size} rules pass` : `${checked?.evaluation.checks.filter((c) => !c.passed).length ?? 0} rule breach${checked?.evaluation.checks.filter((c) => !c.passed).length === 1 ? "" : "es"}`}
                    </span>
                  </div>
                  {Object.entries(edits).map(([cs, p]) => {
                    const a = agreed!.assignments.find((x) => x.callsign === cs)!;
                    const fail = checked?.evaluation.checks.filter((c) => !c.passed && c.callsign === cs) ?? [];
                    const sug = checked?.suggestions[cs];
                    const parts = [
                      p.runway && p.runway !== a.runway ? `${a.runway} → ${p.runway}` : null,
                      p.slot_min != null && p.slot_min !== a.slot_min ? `${clock(wave.window_start, a.slot_min ?? 0)} → ${clock(wave.window_start, p.slot_min)}` : null,
                      p.gate && p.gate !== a.gate ? `gate ${a.gate} → ${p.gate}` : null,
                    ].filter(Boolean);
                    return (
                      <div key={cs} className="ec-row">
                        <div className="ec-what"><button className="linkish mono" onClick={() => setSelected(cs)}>{icao(cs)}</button> <span className="mono">{parts.join(" · ")}</span></div>
                        {fail.map((c) => <div key={c.rule} className="ec-fail">{RULE_NAME[c.rule] ?? c.rule}: {icaoText(c.detail)}</div>)}
                        <div className="ec-acts">
                          {sug != null && <button className="btn sm primary" onClick={() => edit(cs, { runway: p.runway ?? a.runway, slot_min: sug })}>Move to {clock(wave.window_start, sug)}, nearest legal</button>}
                          <button className="btn sm" onClick={() => revert(cs)}>Revert</button>
                        </div>
                      </div>
                    );
                  })}
                  {[...new Set((checked?.evaluation.checks ?? []).filter((c) => !c.passed && c.callsign && !edits[c.callsign]).map((c) => c.callsign!))].map((cs) => {
                    const a = current?.assignments.find((x) => x.callsign === cs);
                    const sug = checked?.suggestions[cs];
                    return (
                      <div key={cs} className="ec-row">
                        <div className="ec-what"><button className="linkish mono" onClick={() => setSelected(cs)}>{icao(cs)}</button> <span className="muted">not moved, now affected</span></div>
                        {checked!.evaluation.checks.filter((c) => !c.passed && c.callsign === cs).map((c) => <div key={c.rule} className="ec-fail">{RULE_NAME[c.rule] ?? c.rule}: {icaoText(c.detail)}</div>)}
                        {sug != null && a && <div className="ec-acts"><button className="btn sm primary" onClick={() => edit(cs, { runway: a.runway, slot_min: sug })}>Move {icao(cs)} to {clock(wave.window_start, sug)}, nearest legal</button></div>}
                      </div>
                    );
                  })}
                  {/* with one edit its own Revert says it all; Undo and Revert all earn a place only past that */}
                  {(undo.length > 1 || nEdits > 1) && <div className="ec-foot">{undo.length > 1 && <button className="btn text sm" onClick={undoLast}>Undo</button>}{nEdits > 1 && <button className="btn text sm" onClick={() => revert()}>Revert all</button>}</div>}
                </div>
              )}

              {decision ? (
                <p className={`decided ${decision.kind === "APPROVE" ? "pos" : "warn"}`}>
                  {decision.kind === "APPROVE" ? "Approved" : "Overridden"}{decision.edits ? ` with ${decision.edits} edit${decision.edits === 1 ? "" : "s"}` : ""} at {new Date(decision.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · {decision.offline ? "offline demo, not logged" : "logged"}
                </p>
              ) : overriding ? (
                <div className="decide">
                  <label htmlFor="note">Why are you overriding this plan?</label>
                  <textarea id="note" required aria-required="true" value={note} onChange={(e) => setNote(e.target.value)} placeholder={`For example: holding ${focusName ?? "the flight"} at the gate; its crew is out of hours`} />
                  <div className="dec-actions"><button className="btn text" onClick={() => setOverriding(false)}>Cancel</button><button className="btn" disabled={!note.trim() || deciding} onClick={() => decide("OVERRIDE")}>Record override</button></div>
                </div>
              ) : (
                <>
                  {!nEdits && <p className="hint">Drag a bar on the timeline below to move a flight; the plan is re-checked as you go.</p>}
                  <div className="dec-actions">
                    <button className="btn" disabled={deciding} onClick={() => setOverriding(true)}>Override</button>
                    <button className="btn primary wide" disabled={deciding || blocked} onClick={() => decide("APPROVE")}
                      title={blocked ? "Fix, revert or move the edited flights so every rule passes" : undefined}>
                      {blocked && !checking ? "Approve is locked" : nEdits ? `Approve with ${nEdits} edit${nEdits === 1 ? "" : "s"}` : "Approve plan"}
                    </button>
                  </div>
                </>
              )}
              {status === "done" && !decision && <button className="btn text sm again" onClick={negotiate}>Run the negotiation again</button>}
            </div>
          )}
        </aside>
      </div>

      <section className="card plan-card" aria-label="Runways and gates">
        <div className="card-head">
          <h2>Runways and gates</h2>
          {result ? (
            <div className="seg" role="group" aria-label="Plan shown">
              <button aria-pressed={view === "before"} onClick={() => setView("before")} title="The schedule as it stood, first come first served">Before</button>
              <button aria-pressed={view === "compare"} onClick={() => setView("compare")} title="The plan, with where each flight was before as a dashed outline">Compare</button>
              <button aria-pressed={view === "after"} onClick={() => setView("after")} title="The plan the agents agreed, with your edits">After</button>
            </div>
          ) : <span className="meta">{table ? (lastTurn ? `Proposal from ${agentName(lastTurn.sender)}, turn ${lastTurn.round}` : "Opening plan, first come first served") : "Other traffic only, no plan yet"}</span>}
          <span className="meta push">{canEdit ? `Drag a bar to change its runway, time or gate · arrow keys nudge · Esc cancels · ${UNDO_KEY} undoes` : view === "before" ? "The status quo can't be edited" : decision ? "Decided; the plan is locked" : "Scrub the clock on the time axis"}</span>
        </div>
        <Gantt wave={wave} plan={shown} baseline={baseline} agreed={nEdits ? agreed : null} view={result ? view : "after"} turn={turn}
          rejection={view !== "before" ? rejection : null} tone={tone} involved={involved} editable={canEdit} onEdit={edit}
          t={tNow} onTime={(v) => { setPlaying(false); setT(v); }} selected={selected} onSelect={setSelected}
          paths={pathsNote ? null : paths} onDrag={(cs, p) => setDragAt(p ? { cs, patch: p } : null)} preview={preview} previewFailed={previewErr} />
        <div className="gantt-foot">
          <GanttLegend view={result ? view : "after"} negotiated={!!result} focus={wave.focus ?? undefined} edited={nEdits > 0}
            simulated={wave.gates.some((g) => g.busy.some((b) => /simulated/i.test(b.label)))}
            holds={(shown?.assignments ?? []).some((a) => a.metrics.hold_min > 0) || !!paths?.air.some((a) => a.hold && a.hold.laps >= 1)}
            taxi={(shown?.assignments ?? []).some((a) => a.action === "land" && a.slot_min != null && (a.metrics.in_block_min ?? 0) > a.slot_min)} />
          {result && view === "compare" && <span className="meta">{changes(result.baseline.plan, current, wave.window_start)}</span>}
        </div>
      </section>

      <div className="row2">
        <section className="card feed-card">
          <div className="feed-head">
            <h2>Negotiation</h2>
            <span className="meta">{status === "running" ? `turn ${Math.max(1, lastTurn?.round ?? 1)} of 3` : result ? `${result.stats.model_calls} model calls · ${result.stats.fallbacks} fallback${result.stats.fallbacks === 1 ? "" : "s"}` : "3 agents · 12 rules"}</span>
          </div>
          {result?.narration && <p className="brief">{icaoText(result.narration.brief)}<span className="brief-src"> {result.narration.source === "claude" ? `Written by ${result.narration.model ?? "Claude"} from the log; it decides nothing.` : "From a template over the log."}</span></p>}
          {result?.model_errors.length ? <p className="warnline">{result.model_errors.join(" · ")}</p> : null}
          <div className="feed"><Log messages={messages} running={status === "running"} focus={result?.focus ?? wave.focus} details={details} /></div>
          {messages.length > 0 && <label className="check foot"><input type="checkbox" checked={details} onChange={(e) => setDetails(e.target.checked)} />Show model, probability and latency</label>}
        </section>
        <section className="card flights-card">
          <div className="card-head"><h2>Flights in the plan</h2><span className="meta">{result ? `${currentEv?.totals.delay_min ?? "…"} min gate delay · status quo ${result.baseline.evaluation.totals.delay_min}` : `${wave.flights.length} negotiated`}</span></div>
          <PlanTable wave={wave} plan={view === "before" && result ? result.baseline.plan : current} baseline={result?.baseline.plan ?? null} edited={new Set(Object.keys(edits))} />
        </section>
      </div>

      <section className="card below">
        <div className="tabs" role="tablist" aria-label="Details" onKeyDown={onTabKey}>
          {TABS.map((x) => (
            <button key={x.id} id={`tab-${x.id}`} role="tab" aria-selected={tab === x.id} aria-controls={`panel-${x.id}`} tabIndex={tab === x.id ? 0 : -1} onClick={() => setTab(x.id)}>{x.label}</button>
          ))}
        </div>
        <div className="tabpanel" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === "rules" && <Checks key={`${result?.run_id ?? "none"}-${checked?.plan.id ?? ""}`} wave={wave} ev={currentEv} base={result?.baseline.evaluation ?? null} />}
          {tab === "data" && <Weather wave={wave} />}
          {tab === "limits" && <Limits limits={result?.limits ?? null} />}
          {tab === "sources" && <Sources wave={wave} />}
        </div>
      </section>
      <p className="sr-only" aria-live="polite">{status === "running" ? `Negotiating, turn ${lastTurn?.round ?? 1} of 3` : status === "done" ? (nEdits ? (checking ? "Re-checking your edit" : checked?.evaluation.feasible ? "Your edit passes every rule" : "Your edit breaks a rule; approve is locked") : "The plan is ready to approve, edit or override.") : ""}</p>
    </main>
  );
}

function Revision({ r, plan, wave }: { r: NegotiationResult; plan: JointPlan | null; wave: WaveScenario }) {
  const a = plan?.assignments.find((x) => x.callsign === r.focus);
  const f = wave.flights.find((x) => x.callsign === r.focus);
  const name = icao(r.revision.callsign);
  if (!a || a.action === "divert" || !f) return <p className="dec-head">{name} diverts to {r.revision.alternate ?? f?.fuel.alternate ?? "its alternate"}</p>;
  // in-block delay against the scheduled gate time (BTS STA); a landing slot minus STA would mix time bases
  const late = Math.round(a.metrics.delay_min);
  return (
    <>
      <p className="dec-head">{name} lands <span className="mono">{clock(wave.window_start, a.slot_min ?? 0)}</span> on {a.runway}, gate {a.gate}</p>
      <p className="dec-line"><span className={late > 0 ? "neg" : "pos"}>{signed(late, " min")}</span> at the gate against its schedule (STA {clock(wave.window_start, f.sched_min)}){a.gate !== f.scheduled_gate ? `; gate was ${f.scheduled_gate}` : ""}{a.metrics.hold_min ? `; holds ${a.metrics.hold_min} min` : ""}.</p>
    </>
  );
}

function Kpis({ ev, base, pending }: { ev: PlanEvaluation | null; base: PlanEvaluation; pending: boolean }) {
  return <div className={`mini-kpis${pending ? " pending" : ""}`}><Outcome ev={ev} base={base} /></div>;
}

function intro(w: WaveScenario): string {
  const f = w.flights.find((x) => x.callsign === w.focus);
  if (!f) return `${w.flights.length} flights in the plan.`;
  const why = f.delays.weather >= f.delays.late_aircraft ? `${f.delays.weather} min weather delay` : `${f.delays.late_aircraft} min late aircraft`;
  return `${f.aircraft_type ?? "Aircraft"} from ${f.origin}, scheduled ${clock(w.window_start, f.sched_min)} at gate ${f.scheduled_gate}; ${why}, ${f.connections.reduce((s, c) => s + c.pax, 0)} connecting passengers.`;
}

function lastRadio(msgs: NegotiationMessage[]): { who: string; cls: string; text: string } | null {
  const m = [...msgs].reverse().find((x) => x.body.type === "AGENT_TURN" || x.body.type === "OPEN" || x.body.type === "CHECK" || x.body.type === "CONSENSUS");
  if (!m) return null;
  const b = m.body;
  const who = agentName(m.sender).replace(/^Pilot (\S+)/, (_, cs) => `Pilot ${icao(cs)}`);
  const cls = m.sender.startsWith("PILOT") ? "pilot" : m.sender.toLowerCase();
  const text = b.type === "AGENT_TURN" ? (b.moves.length ? b.moves.map((x) => x.move.label).join("; ") : "keeps the plan on the table")
    : b.type === "OPEN" ? "opens with the first-come plan" : b.type === "CHECK" ? (b.passed ? "plan passes every rule" : `vetoed: ${b.violations.map((v) => v.rule).join(", ")}`)
    : b.type === "CONSENSUS" ? `all ${b.accepted_by.length} agents accept` : "";
  return { who, cls, text: icaoText(text) };
}

function changes(base: JointPlan, now: JointPlan | null, ws: string): string {
  if (!now) return "";
  const moved = now.assignments.filter((a) => {
    const b = base.assignments.find((x) => x.callsign === a.callsign);
    return b && (b.runway !== a.runway || b.slot_min !== a.slot_min || b.gate !== a.gate || b.action !== a.action);
  });
  if (!moved.length) return "Nothing moved from the status quo.";
  return `${moved.length} of ${now.assignments.length} flights moved: ${moved.map((a) => { const b = base.assignments.find((x) => x.callsign === a.callsign)!; return `${icao(a.callsign)} ${b.runway}→${a.runway}${b.slot_min !== a.slot_min ? ` ${clock(ws, b.slot_min ?? 0)}→${clock(ws, a.slot_min ?? 0)}` : ""}`; }).join(", ")}`;
}

const HARD_CASES = [
  { id: "F2015-6291", title: "ORD, every runway closed", what: "no legal landing: solver diverts to MDW" },
  { id: "F2015-70412", title: "PHX, every runway closed", what: "no legal landing: solver diverts to TUS" },
  { id: "F2015-252123", title: "LGA, runway closed, 5 queued", what: "agreement only in turn 3" },
  { id: "F2015-247837", title: "SLC, 3 of 6 runways closed", what: "agreement only in turn 3" },
  { id: "F2015-102056", title: "DL2019, nothing passes the rules", what: "solver safety net" },
];

function FlightPicker({ airport, onPick }: { airport: string; onPick: (waveId: string) => void }) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<FlightRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const tm = setTimeout(() => {
      api.flights(q, q ? "" : airport).then((r) => { setRows(r.flights); setTotal(r.total); }).catch(() => setRows([]));
    }, 250);
    return () => clearTimeout(tm);
  }, [q, airport]);
  return (
    <details className="settings" ref={ref}>
      <summary className="select search-trigger" aria-label="Search all flights.csv rows">Search a flight or airport</summary>
      <div className="menu wide right">
        <label className="field">Search by flight, airport or row
          <input autoFocus className="select" style={{ backgroundImage: "none", cursor: "text" }} value={q} onChange={(e) => setQ(e.target.value)} placeholder={`For example AA1298, or ${airport}`} />
        </label>
        {!q && (
          <div className="picklist" role="list" aria-label="Hard scenarios">
            <p className="muted" style={{ fontSize: 13 }}>Hard scenarios</p>
            {HARD_CASES.map((h) => (
              <button key={h.id} role="listitem" onClick={() => { ref.current?.removeAttribute("open"); onPick(h.id); }}>
                <span><span className="code">{h.id}</span> {h.title}</span><span className="muted">{h.what}</span>
              </button>
            ))}
            <p className="muted" style={{ fontSize: 13 }}>Flights at {airport}</p>
          </div>
        )}
        <div className="picklist" role="list">
          {rows == null ? <p className="muted">Loading…</p> : !rows.length ? <p className="muted">No flights match.</p> : rows.map((r) => (
            <button key={r.row_id} role="listitem" onClick={() => { ref.current?.removeAttribute("open"); onPick(r.wave_id); }}>
              <span><span className="code">{icao(r.flight)}</span> {r.origin} → {r.dest}</span>
              <span className="muted">{r.date} {r.sched_arr} · {r.arr_delay > 0 ? `+${r.arr_delay} min` : "on time"}</span>
            </button>
          ))}
        </div>
        {rows && total > rows.length && <p className="muted" style={{ fontSize: 13 }}>Showing {rows.length} of {total.toLocaleString("en-US")}; search to narrow.</p>}
      </div>
    </details>
  );
}
