import { useEffect, useState } from "react";
import { api, ApiError, type BenchmarkDataset } from "./api/client";
import type { BenchmarkReport, ModelInfo } from "./api/types";
import { dateLabel } from "./format";

type Arm = BenchmarkReport["arms"][number];
const COLS: { key: keyof Arm["summary"]; label: string; unit: string; digits: number; goodDown: boolean }[] = [
  { key: "consensus_rate_pct", label: "Consensus", unit: "%", digits: 1, goodDown: false },
  { key: "mean_rounds", label: "Rounds", unit: "", digits: 1, goodDown: true },
  { key: "rule_violations_in_agreed_plans", label: "Rule breaches", unit: "", digits: 0, goodDown: true },
  { key: "delay_min", label: "Delay", unit: " min", digits: 1, goodDown: true },
  { key: "pax_protected_pct", label: "Pax protected", unit: "%", digits: 1, goodDown: false },
  { key: "fuel_kg", label: "Hold fuel", unit: " kg", digits: 0, goodDown: true },
  { key: "brier", label: "Brier", unit: "", digits: 3, goodDown: true },
  { key: "latency_p95_ms", label: "p95 latency", unit: " ms", digits: 0, goodDown: true },
  { key: "error_rate_pct", label: "Errors", unit: "%", digits: 1, goodDown: true },
  { key: "cost_usd_per_wave", label: "Cost / wave", unit: "", digits: 3, goodDown: true },
];

export function Benchmark() {
  const [rep, setRep] = useState<BenchmarkReport | null>(null);
  const [err, setErr] = useState<{ code: string; message: string } | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [dataset, setDataset] = useState<BenchmarkDataset>("bts2024");
  useEffect(() => { api.config().then((c) => setModels(c.models)).catch(() => setModels([])); }, []);
  useEffect(() => {
    let gone = false;
    setRep(null); setErr(null);
    api.benchmark(dataset).then((r) => { if (!gone) setRep(r); })
      .catch((e) => { if (!gone) setErr(e instanceof ApiError ? { code: e.code, message: e.message } : { code: "error", message: String(e) }); });
    return () => { gone = true; };
  }, [dataset]);

  // the backend's reasons mix the verdict with column definitions and per-arm latency lines already in the table
  const reasons = rep?.selection.reasons ?? [];
  const isNote = (r: string) => /=|measured against|^outage test/i.test(r);
  const why = reasons.filter((r) => !isNote(r) && !/p95 all decisions/i.test(r));
  // the backend writes its footnotes with field names; say them in words
  const noteText = (r: string) =>
    /^Brier is measured/i.test(r) ? "Brier compares each model's accept probability with the formula's verdict, not with ground truth (lower is closer)."
    : /^cost_usd_per_wave/i.test(r) ? "Cost: output tokens are free on this route and input cost isn't measured here, so it reads as not measured (the README estimates about $0.0005 a wave)."
    : /^fallback_rate_pct/i.test(r) ? "Fallback: share of waves the deterministic solver had to finish. Errors: share of decisions answered by the next model after this one failed."
    : r;
  const notes = reasons.filter((r) => isNote(r) && !/^outage test/i.test(r)).map(noteText);
  const symbols = (t: string) => t.replace(/>=/g, "≥").replace(/<=/g, "≤").replace(/->/g, "→");
  return (
    <main className="canvas">
      <header className="titlerow">
        <div>
          <h1>Benchmark</h1>
          <p className="sub">{rep ? <>{rep.n_waves} real waves, seed {rep.seed}, every arm on the same waves. <b>Winner: {rep.selection.winner}</b> ({symbols(rep.selection.rule)}).</> : "Each decision model against the formulas and the status quo on the same real waves."}</p>
        </div>
        <div className="toolbar">
          <div className="seg" role="group" aria-label="Waves benchmarked">
            <button aria-pressed={dataset === "bts2024"} onClick={() => setDataset("bts2024")} title="Busy hours from the US DOT 2024 arrivals, several late flights each">2024 busy hours</button>
            <button aria-pressed={dataset === "flights2015"} onClick={() => setDataset("flights2015")} title="Rows of the challenge flights.csv (2015), one disrupted flight each">2015 flights.csv</button>
          </div>
          {rep && <span className="muted" style={{ fontSize: 13 }}>Generated {dateLabel(rep.generated_at)}</span>}
        </div>
      </header>

      {!rep ? (
        <section className="card">
          <div className="card-head"><h2>Model arms</h2><span className="meta">{err ? "Not generated yet" : "Loading…"}</span></div>
          <p className="empty" style={{ marginBottom: 16 }}>{err?.code === "not_found" || err?.code === "404"
            ? "The benchmark has not been run yet. Each arm below gets its figures once the backend has run it on the same real waves."
            : err ? `Couldn't load the benchmark: ${err.message}` : "Loading the benchmark…"}</p>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Model arms, not yet measured">
            <table>
              <thead><tr><th>Model</th>{COLS.map((c) => <th key={c.key} className="num">{c.label}</th>)}</tr></thead>
              <tbody>
                {[...models, { id: "status-quo", provider: "formula", label: "Status quo (first come, first served)", available: true } as ModelInfo].map((m) => (
                  <tr key={m.id}>
                    <td><span className="code">{m.label}</span><span className="sub">{m.id === "status-quo" ? "baseline" : m.provider}{m.available ? "" : " · unavailable"}</span></td>
                    {COLS.map((c) => <td key={c.key} className="num muted">n/a</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <div className="grid" style={{ alignItems: "stretch" }}>
          <section className="card span-12">
            <div className="card-head"><h2>Model arms</h2><span className="meta">Status quo: {rep.baseline.delay_min.toFixed(1)} min delay · {rep.baseline.pax_protected_pct.toFixed(1)}% pax protected · {rep.baseline.fuel_kg} kg hold fuel</span></div>
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Model arms">
              <table>
                <thead><tr><th>Model</th>{COLS.map((c) => <th key={c.key} className="num">{c.label}</th>)}</tr></thead>
                <tbody>
                  {rep.arms.map((a) => (
                    <tr key={a.model}>
                      <td><span className="code">{a.label}</span>{a.model === rep.selection.winner && <span className="pill pos" style={{ marginLeft: 8 }}>Selected</span>}<span className="sub">{a.provider}</span></td>
                      {COLS.map((c) => {
                        const v = a.summary[c.key];
                        if (v == null) return <td key={c.key} className="num muted" title={a.provider === "formula" ? "The formula makes no model calls" : "No data for this arm"}>{a.provider === "formula" ? "n/a" : "n/a"}</td>;
                        const bad = c.key === "rule_violations_in_agreed_plans" && v > 0;
                        if (c.key === "cost_usd_per_wave" && v === 0) return <td key={c.key} className="num muted" title={a.provider === "formula" ? "No model calls" : "Input cost not measured in this run"}>{a.provider === "formula" ? "$0" : "not measured"}</td>;
                        return <td key={c.key} className={`num${bad ? " breach" : ""}`}>{c.key === "cost_usd_per_wave" ? `$${v.toFixed(4)}` : `${v.toFixed(c.digits)}${c.unit}`}</td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {(() => {
              // the status quo looks faster only because it ignores the rules; say so where the numbers are
              const b = rep.baseline, win = rep.arms.find((a) => a.model === rep.selection.winner);
              const bad = b.infeasible_ids?.length, ok = b.n_feasible;
              if (!bad || ok == null) return null;
              return <p className="bench-frame">Why every arm shows more delay than the status quo: first come, first served breaks at least one safety rule on {bad} of {rep.n_waves} waves. It lands aircraft inside wake separation or onto occupied gates instead of holding them, which is also why its hold fuel is near zero.
                {b.feasible_only && win?.delay_min_vs_feasible_status_quo != null && <> On the {ok} waves where it is legal, {win.label} averages {win.delay_min_vs_feasible_status_quo.toFixed(1)} min against {b.feasible_only.delay_min.toFixed(1)} min for the status quo.</>}</p>;
            })()}
            {notes.length > 0 && <ul className="notes">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
          </section>
          <section className="card span-6">
            <div className="card-head"><h2>Why {rep.arms.find((a) => a.model === rep.selection.winner)?.label ?? rep.selection.winner}</h2></div>
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>{why.map((r) => <li key={r}>{symbols(r).replace(/(\d+\.\d{2,}) min/g, (_, n: string) => `${Number(n).toFixed(1)} min`)}</li>)}</ul>
            <dl className="kv" style={{ marginTop: 16 }}>
              <dt>Engine switched off</dt><dd>{rep.ablation_no_engine_unsafe_pct == null ? <span className="muted">Not measured in this run</span> : <><span className="breach">{rep.ablation_no_engine_unsafe_pct.toFixed(1)}%</span> of agreed plans break a rule</>}</dd>
              <dt>Outage test</dt><dd>{rep.outage_test.waves === 0 ? <span className="muted">Skipped: no model arm beat the formula, so there was no primary model to disable</span>
                : <>{rep.outage_test.primary_disabled} disabled: {rep.outage_test.resolved} of {rep.outage_test.waves} waves resolved{Object.keys(rep.outage_test.fallbacks_used).length ? `; ${Object.entries(rep.outage_test.fallbacks_used).map(([k, v]) => `${k} answered ${v} decisions instead`).join(", ")}` : ""}</>}</dd>
            </dl>
          </section>
          <section className="card span-6">
            <div className="card-head"><h2>Failures</h2><span className="meta">{rep.failures.length} listed</span></div>
            {rep.failures.length ? (
              <div className="table-wrap" tabIndex={0} role="region" aria-label="Failures"><table><tbody>{rep.failures.map((f) => (
                <tr key={f.wave_id + f.model}><td><a className="code" style={{ color: "var(--text)", textDecoration: "none" }} href={`#/wave/${f.wave_id}`}>{f.wave_id}</a><span className="sub">{f.model}</span></td><td style={{ whiteSpace: "normal" }}>{f.reason}</td></tr>
              ))}</tbody></table></div>
            ) : <p className="empty">No failures in this run.</p>}
          </section>
        </div>
      )}
    </main>
  );
}
