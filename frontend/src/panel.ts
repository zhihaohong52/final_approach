// Which picture the approach panel shows. One rule, kept pure so it is tested: the radar until the disrupted
// flight touches down and the surface map after it (automatic hand-off), unless the duty manager picked one;
// both side by side by default on wide screens. Without a surface for the wave the radar is the only picture.

export type Panel = "auto" | "radar" | "surface" | "both";
export type Shown = "radar" | "surface" | "both";

export interface PanelInput {
  choice: Panel | null;                          // the duty manager's pick; null = the default
  surface: "real" | "generic" | null;            // WaveGeo.surface?.status, null when there is no surface
  canBoth: boolean;                              // the screen is wide enough for both side by side
  touchdown: number | null;                      // the focus flight's touchdown, minutes vs T0
  t: number;                                     // replay clock
}

/** The choice in force: an explicit pick, else Both on a wide screen, else the automatic hand-off. "Both" on a
 *  screen that has become too narrow falls back to the hand-off. */
export function effectiveChoice({ choice, surface, canBoth }: Pick<PanelInput, "choice" | "surface" | "canBoth">): Panel {
  const c = choice ?? (canBoth && surface ? "both" : "auto");
  return c === "both" && !canBoth ? "auto" : c;
}

export function shownPanel(i: PanelInput): Shown {
  if (!i.surface) return "radar";
  const c = effectiveChoice(i);
  if (c !== "auto") return c;
  return i.surface === "real" && i.touchdown != null && i.t >= i.touchdown ? "surface" : "radar";
}
