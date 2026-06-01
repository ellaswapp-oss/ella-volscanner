"use client";

import { clsx } from "clsx";

const TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA"] as const;
export type Ticker = (typeof TICKERS)[number];

interface Props {
  selected: Ticker;
  onChange: (t: Ticker) => void;
  disabled?: boolean;
}

export function TickerSelector({ selected, onChange, disabled }: Props) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Select ticker">
      {TICKERS.map((t) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          disabled={disabled}
          aria-pressed={t === selected}
          className={clsx(
            "rounded-lg px-3 py-1.5 text-xs font-semibold tracking-wide transition-all",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:ring-offset-surface",
            disabled && "opacity-50 cursor-not-allowed",
            t === selected
              ? "bg-white text-surface shadow-sm focus-visible:ring-white"
              : "bg-surface-3 text-slate-400 hover:text-white hover:bg-surface-2 border border-border focus-visible:ring-slate-400"
          )}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

export { TICKERS };
