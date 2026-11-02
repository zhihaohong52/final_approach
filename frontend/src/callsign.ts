// Controllers see ICAO callsigns (SKW5559) and say the airline's telephony ("SkyWest 5559"), not the
// IATA flight number the BTS data carries (OO5559). BTS records the operating carrier, which is whose
// callsign is used. Every carrier in the 2015 and 2024 BTS files is listed; anything else is shown as is.
const CARRIERS: Record<string, [icao: string, say: string]> = {
  AA: ["AAL", "American"], AS: ["ASA", "Alaska"], B6: ["JBU", "JetBlue"], DL: ["DAL", "Delta"],
  EV: ["ASQ", "Acey"], F9: ["FFT", "Frontier Flight"], HA: ["HAL", "Hawaiian"], MQ: ["ENY", "Envoy"],
  NK: ["NKS", "Spirit Wings"], OO: ["SKW", "SkyWest"], UA: ["UAL", "United"], US: ["AWE", "Cactus"],
  VX: ["VRD", "Redwood"], WN: ["SWA", "Southwest"], YX: ["RPA", "Brickyard"], OH: ["JIA", "Blue Streak"],
  "9E": ["EDV", "Endeavor"], G4: ["AAY", "Allegiant"],
};
const RE = /\b([A-Z][A-Z0-9]|[0-9][A-Z])(\d{1,4})\b/g;

const split = (cs: string): [string, string] | null => {
  const m = cs.match(/^([A-Z][A-Z0-9]|[0-9][A-Z])(\d{1,4})$/);
  return m && CARRIERS[m[1]] ? [m[1], m[2]] : null;
};

/** "OO5559" -> "SKW5559" */
export function icao(cs: string): string {
  const p = split(cs);
  return p ? CARRIERS[p[0]][0] + p[1] : cs;
}

/** "OO5559" -> "SkyWest 5559" */
export function spoken(cs: string): string {
  const p = split(cs);
  return p ? `${CARRIERS[p[0]][1]} ${p[1]}` : cs;
}

/** Rewrite every IATA flight number inside a sentence the engine wrote. */
export function icaoText(s: string): string {
  return s.replace(RE, (whole, c: string, n: string) => (CARRIERS[c] ? CARRIERS[c][0] + n : whole));
}
