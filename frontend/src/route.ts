// Hash routes: #/wave is the home page (radar, airport map, Gantt), #/wave/details the second page (negotiation log,
// flights, rules, weather); a wave id rides on either as #/wave/<id> and #/wave/details/<id>. #/benchmark is separate.
export type Route = { screen: "wave" | "benchmark"; page: "home" | "details"; waveId: string | null };

export function parseRoute(hash: string): Route {
  if (hash.startsWith("#/benchmark")) return { screen: "benchmark", page: "home", waveId: null };
  const d = hash.match(/^#\/wave\/details(?:\/(.+))?$/);
  if (d) return { screen: "wave", page: "details", waveId: d[1] ? decodeURIComponent(d[1]) : null };
  const w = hash.match(/^#\/wave\/(.+)$/);
  return { screen: "wave", page: "home", waveId: w ? decodeURIComponent(w[1]) : null };
}

export function waveHash(page: "home" | "details", waveId: string | null): string {
  const id = waveId ? `/${encodeURIComponent(waveId)}` : "";
  return page === "details" ? `#/wave/details${id}` : `#/wave${id}`;
}
