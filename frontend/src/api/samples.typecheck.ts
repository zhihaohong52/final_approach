// Spec §12: v2.1 sample JSON must stay valid under the v2.2 types. Compiled by `npx tsc --noEmit`.
import type { WaveScenario, NegotiationResult, BenchmarkReport } from "./types";
import wave from "./samples/v2.1/wave.json";
import result from "./samples/v2.1/result.json";
import bench from "./samples/v2.1/benchmark.json";
// `as` only because JSON string unions widen to `string`; result and wave go through `unknown` because the
// inferred JSON `evidence` objects are unions with `undefined` members, which `Record<string, string | number>` rejects.
export const _w: WaveScenario = wave as unknown as WaveScenario;
export const _r: NegotiationResult = result as unknown as NegotiationResult;
export const _b: BenchmarkReport = bench as BenchmarkReport;
