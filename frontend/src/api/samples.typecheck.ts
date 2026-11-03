// Spec §12: v2.1 sample JSON must stay valid under the v2.2 types. Compiled by `npx tsc --noEmit`.
// Checked by assignability (no casts), so a missing or retyped field anywhere in the tree is an error.
import type { WaveScenario, NegotiationResult, BenchmarkReport } from "./types";
import wave from "./samples/v2.1/wave.json";
import result from "./samples/v2.1/result.json";
import benchmark from "./samples/v2.1/benchmark.json";

// JSON imports infer `string` for string unions, and `evidence` objects with `undefined` members.
// Widen exactly that; optionality and everything else is kept as declared.
type Widen<T> = T extends string ? string
  : T extends object
    ? (string extends keyof T ? Record<string, string | number | undefined> : { [K in keyof T]: Widen<T[K]> })
    : T;

const chk = <X,>(_: X): void => {};
chk<Widen<WaveScenario>>(wave);
chk<Widen<NegotiationResult>>(result);
chk<Widen<BenchmarkReport>>(benchmark);

// Negative controls: each deliberately broken copy must be rejected.
const { flights: _f, ...noFlights } = wave;
// @ts-expect-error missing top-level `flights`
chk<Widen<WaveScenario>>(noFlights);

const { callsign: _c, ...noCallsign } = wave.flights[0];
// @ts-expect-error a flight without `callsign`
chk<Widen<WaveScenario>>({ ...wave, flights: [noCallsign, ...wave.flights.slice(1)] });

// @ts-expect-error a flight with `callsign: 1`
chk<Widen<WaveScenario>>({ ...wave, flights: [{ ...wave.flights[0], callsign: 1 }, ...wave.flights.slice(1)] });
