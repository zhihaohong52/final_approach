// Spec §12: v2.1 sample JSON must stay valid under the v2.2 types. Compiled by `npx tsc --noEmit`.
// Checked by assignability (no casts), so a missing or retyped field anywhere in the tree is an error.
import type { WaveScenario, NegotiationResult, BenchmarkReport, WaveGeo, TrajectorySet, EvaluateResponse } from "./types";
import wave from "./samples/v2.1/wave.json";
import result from "./samples/v2.1/result.json";
import benchmark from "./samples/v2.1/benchmark.json";
import geo from "./samples/geo.json";                    // v2.2 part 2
import trajectories from "./samples/trajectories.json";
import result22 from "./samples/result.json";

// JSON imports infer `string` for string unions, and `evidence` objects with `undefined` members.
// Widen exactly that (and tuples such as `[lat, lon]` or `[t_min, lat, lon]`, which JSON infers as plain
// arrays of their element union); optionality and everything else is kept as declared.
type Widen<T> = T extends string ? string
  : T extends [unknown, ...unknown[]] ? Widen<T[number]>[]
  : T extends object
    ? (string extends keyof T ? Record<string, Widen<T[string & keyof T]> | undefined> : { [K in keyof T]: Widen<T[K]> })
    : T;

const chk = <X,>(_: X): void => {};
chk<Widen<WaveScenario>>(wave);
chk<Widen<NegotiationResult>>(result);
chk<Widen<BenchmarkReport>>(benchmark);
chk<Widen<WaveGeo>>(geo);
chk<Widen<TrajectorySet>>(trajectories);
chk<Widen<NegotiationResult>>(result22);
chk<Widen<EvaluateResponse>>({ plan: result22.final_plan, evaluation: result22.evaluation, suggestions: {}, trajectories });

// Negative controls: each deliberately broken copy must be rejected.
const { flights: _f, ...noFlights } = wave;
// @ts-expect-error missing top-level `flights`
chk<Widen<WaveScenario>>(noFlights);

const { callsign: _c, ...noCallsign } = wave.flights[0];
// @ts-expect-error a flight without `callsign`
chk<Widen<WaveScenario>>({ ...wave, flights: [noCallsign, ...wave.flights.slice(1)] });

// @ts-expect-error a flight with `callsign: 1`
chk<Widen<WaveScenario>>({ ...wave, flights: [{ ...wave.flights[0], callsign: 1 }, ...wave.flights.slice(1)] });
