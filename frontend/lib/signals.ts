/**
 * lib/signals.ts
 * Signal color/label utilities shared across all dashboard cards.
 */

export type SignalKey =
  | "BUY_PREMIUM"
  | "NEUTRAL"
  | "SELL_SELECTIVE"
  | "SELL_AGGRESSIVE";

export interface SignalStyles {
  label:       string;
  shortLabel:  string;
  text:        string;   // Tailwind text color
  bg:          string;   // card/badge background
  border:      string;   // border color
  dot:         string;   // dot indicator color
  badge:       string;   // combined badge classes
  scoreBg:     string;   // score number background
}

const SIGNAL_MAP: Record<SignalKey, SignalStyles> = {
  BUY_PREMIUM: {
    label:      "Buy Premium",
    shortLabel: "Buy Vol",
    text:       "text-green-400",
    bg:         "bg-green-500/10",
    border:     "border-green-500/30",
    dot:        "bg-green-400",
    badge:      "bg-green-500/15 text-green-300 border border-green-500/30",
    scoreBg:    "bg-green-500/10",
  },
  NEUTRAL: {
    label:      "Neutral",
    shortLabel: "Neutral",
    text:       "text-yellow-400",
    bg:         "bg-yellow-500/10",
    border:     "border-yellow-500/30",
    dot:        "bg-yellow-400",
    badge:      "bg-yellow-500/15 text-yellow-300 border border-yellow-500/30",
    scoreBg:    "bg-yellow-500/10",
  },
  SELL_SELECTIVE: {
    label:      "Sell Selectively",
    shortLabel: "Sell Sel.",
    text:       "text-orange-400",
    bg:         "bg-orange-500/10",
    border:     "border-orange-500/30",
    dot:        "bg-orange-400",
    badge:      "bg-orange-500/15 text-orange-300 border border-orange-500/30",
    scoreBg:    "bg-orange-500/10",
  },
  SELL_AGGRESSIVE: {
    label:      "Sell Aggressively",
    shortLabel: "Sell Agg.",
    text:       "text-red-400",
    bg:         "bg-red-500/10",
    border:     "border-red-500/30",
    dot:        "bg-red-400",
    badge:      "bg-red-500/15 text-red-300 border border-red-500/30",
    scoreBg:    "bg-red-500/10",
  },
};

export function getSignalStyles(signal: string): SignalStyles {
  return SIGNAL_MAP[signal as SignalKey] ?? SIGNAL_MAP.NEUTRAL;
}

/** Numeric regime score → nearest SignalKey bucket */
export function scoreToSignal(score: number): SignalKey {
  if (score < 30)  return "BUY_PREMIUM";
  if (score < 50)  return "NEUTRAL";
  if (score < 70)  return "SELL_SELECTIVE";
  return "SELL_AGGRESSIVE";
}
