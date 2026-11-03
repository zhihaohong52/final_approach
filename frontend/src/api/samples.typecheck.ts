// Spec §12: v2.1 sample JSON must stay valid under the v2.2 types. Compiled by `npx tsc --noEmit`.
import type { WaveScenario, NegotiationResult, BenchmarkReport } from "./types";
import wave from "./samples/v2.1/wave.json";
import result from "./samples/v2.1/result.json";
import bench from "./samples/v2.1/benchmark.json";

// Inferred JSON `evidence` objects carry `undefined` members; widen only that (nothing else is loosened).
type Loosen<T> = T extends (infer U)[] ? Loosen<U>[]
  : T extends Record<string, string | number> ? Record<string, string | number | undefined>
  : T extends object ? { [K in keyof T]: Loosen<T[K]> } : T;

// `as` only because JSON string unions widen to `string`.
export const _w: Loosen<WaveScenario> = wave as Loosen<WaveScenario>;
export const _r: Loosen<NegotiationResult> = result as Loosen<NegotiationResult>;
export const _b: BenchmarkReport = bench as BenchmarkReport;

// Negative control: a sample missing a required field must be rejected, so a vacuous check is noticed.
const { flights: _omit, ...brokenWave } = wave;
// @ts-expect-error missing `flights`
export const _bad: Loosen<WaveScenario> = brokenWave as Loosen<WaveScenario>;
