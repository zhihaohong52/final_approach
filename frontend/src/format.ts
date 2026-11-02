import type { AgentId, Sender } from "./api/types";

/** Airport-local clock time for a minute offset from the window start (keeps the ISO string's own offset). */
/** hh:mm:ss for the replay clock, which steps once per radar scan */
export function clockSec(windowStart: string, min: number): string {
  const m = windowStart.match(/T(\d{2}):(\d{2})/);
  if (!m) return clock(windowStart, min);
  const total = ((+m[1] * 60 + +m[2]) * 60 + Math.floor(min * 60) + 86400) % 86400;
  return [Math.floor(total / 3600), Math.floor(total / 60) % 60, total % 60].map((v) => String(v).padStart(2, "0")).join(":");
}

export function clock(windowStart: string, min: number): string {
  const m = windowStart.match(/T(\d{2}):(\d{2})/);
  if (!m) return `T${min >= 0 ? "+" : ""}${min}`;
  const total = (+m[1] * 60 + +m[2] + Math.round(min) + 1440) % 1440;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

export function dateLabel(iso: string): string {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${+m[3]} ${months[+m[2] - 1]} ${m[1]}`;
}

// one decimal at most, so float noise (339.3 - 339.6) never reaches the screen
export const signed = (n: number, unit = "") => {
  const m = Math.round(Math.abs(n) * 10) / 10;
  return m === 0 ? `0${unit}` : `${n > 0 ? "+" : "−"}${m}${unit}`;
};
export const fmtInt = (n: number) => n.toLocaleString("en-US");

export function agentClass(s: Sender): string {
  if (s.startsWith("PILOT")) return "pilot";
  return s.toLowerCase();
}
export const agentName = (s: Sender | AgentId) => (s.startsWith("PILOT:") ? `Pilot ${s.slice(6)}` : s === "ATC" ? "ATC" : s === "GROUND" ? "Ground" : s === "ENGINE" ? "Engine" : "Triage");
