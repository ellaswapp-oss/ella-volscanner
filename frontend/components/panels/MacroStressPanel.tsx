"use client";

import { Card, CardHeader, CardBody } from "@/components/Card";
import type { MacroData } from "@/lib/types";
import { clsx } from "clsx";

// ---------------------------------------------------------------------------
// Colour helpers
// ---------------------------------------------------------------------------
function vixColor(v: number): "green" | "yellow" | "orange" | "red" {
  if (v < 15) return "green";
  if (v < 20) return "yellow";
  if (v < 30) return "orange";
  return "red";
}

const COLOR_CLS = {
  green:  "text-vol-green  bg-vol-green/10  border-vol-green/20",
  yellow: "text-vol-yellow bg-vol-yellow/10 border-vol-yellow/20",
  orange: "text-vol-orange bg-vol-orange/10 border-vol-orange/20",
  red:    "text-vol-red    bg-vol-red/10    border-vol-red/20",
};

const METER_COLOR = {
  green:  "bg-vol-green",
  yellow: "bg-vol-yellow",
  orange: "bg-vol-orange",
  red:    "bg-vol-red",
};

// ---------------------------------------------------------------------------
// Visual meter — 0-100 fill bar beneath the metric
// ---------------------------------------------------------------------------
function Meter({
  pct,
  color,
}: {
  pct: number;           // 0-100, how full
  color: keyof typeof METER_COLOR;
}) {
  return (
    <div className="mt-1.5 h-1 w-full rounded-full bg-surface-3 overflow-hidden">
      <div
        className={clsx("h-full rounded-full transition-all duration-700", METER_COLOR[color])}
        style={{ width: `${Math.min(Math.max(pct, 0), 100)}%` }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Macro metric row
// ---------------------------------------------------------------------------
function MacroRow({
  label,
  value,
  note,
  color,
  meterPct,
}: {
  label: string;
  value: string;
  note?: string;
  color: keyof typeof COLOR_CLS;
  meterPct: number;
}) {
  return (
    <div className="py-2.5 border-b border-border last:border-0">
      <div className="flex items-center justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-slate-300">{label}</p>
          {note && <p className="text-[10px] text-slate-600">{note}</p>}
        </div>
        <span
          className={clsx(
            "ml-3 shrink-0 rounded border px-2 py-0.5 font-mono text-sm font-bold",
            COLOR_CLS[color]
          )}
        >
          {value}
        </span>
      </div>
      <Meter pct={meterPct} color={color} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// VIX spotlight (featured metric)
// ---------------------------------------------------------------------------
function VIXSpotlight({ vix, regime }: { vix: number; regime: string }) {
  const color = vixColor(vix);
  const pct = Math.min((vix / 50) * 100, 100); // 50 = extreme stress
  const zoneLbl = vix < 15 ? "Low vol" : vix < 20 ? "Normal" : vix < 30 ? "Elevated" : "Spike";

  return (
    <div
      className={clsx(
        "rounded-lg border p-3 mb-3",
        COLOR_CLS[color].split(" ").slice(1).join(" ")
      )}
    >
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-widest text-slate-500">VIX · Fear Index</p>
          <p className={clsx("mt-0.5 font-mono text-3xl font-bold", COLOR_CLS[color].split(" ")[0])}>
            {vix.toFixed(1)}
          </p>
          <p className="text-xs text-slate-500">{zoneLbl} — {regime}</p>
        </div>
        {/* Mini arc gauge using SVG */}
        <svg width={64} height={38} viewBox="0 0 64 38" aria-hidden>
          {/* Track */}
          <path d="M 8 32 A 24 24 0 0 1 56 32" fill="none" stroke="#2d3148" strokeWidth={7} strokeLinecap="round" />
          {/* Fill */}
          <path
            d="M 8 32 A 24 24 0 0 1 56 32"
            fill="none"
            stroke={
              color === "green" ? "#22c55e"
              : color === "yellow" ? "#eab308"
              : color === "orange" ? "#f97316"
              : "#ef4444"
            }
            strokeWidth={7}
            strokeLinecap="round"
            strokeDasharray={75.4}
            strokeDashoffset={75.4 * (1 - pct / 100)}
            style={{ transition: "stroke-dashoffset 0.7s ease" }}
          />
          <text x={32} y={32} textAnchor="middle" fontSize={9} fill="#64748b" fontFamily="monospace">
            {pct.toFixed(0)}%
          </text>
        </svg>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------
export function MacroStressPanel({ macro }: { macro: MacroData }) {
  const { vix, vix_regime, credit_spread, yield_curve, financial_conditions } = macro;

  // Normalise each metric to a 0-100 stress meter
  const creditPct   = Math.min((credit_spread / 8) * 100, 100);    // 8% = extreme
  const curvePct    = yield_curve < 0
    ? Math.min(Math.abs(yield_curve) / 2 * 100, 100)               // inverted = stress
    : 0;
  const nfciPct     = financial_conditions > 0
    ? Math.min((financial_conditions / 2) * 100, 100)              // positive = tight
    : 0;

  return (
    <Card>
      <CardHeader
        title="Macro Stress"
        subtitle="FRED placeholders — wire live data in Phase 3"
      />
      <CardBody className="space-y-0">
        <VIXSpotlight vix={vix} regime={vix_regime} />

        <MacroRow
          label="HY Credit Spread"
          value={`${credit_spread.toFixed(2)}%`}
          note="OAS vs Treasuries · FRED: BAMLH0A0HYM2"
          color={credit_spread > 5 ? "red" : credit_spread > 3 ? "orange" : credit_spread > 2 ? "yellow" : "green"}
          meterPct={creditPct}
        />
        <MacroRow
          label="10Y – 2Y Yield Curve"
          value={`${yield_curve >= 0 ? "+" : ""}${yield_curve.toFixed(2)}%`}
          note={yield_curve < 0 ? "Inverted — recession signal" : "Normal slope"}
          color={yield_curve < -0.25 ? "red" : yield_curve < 0 ? "orange" : yield_curve < 0.5 ? "yellow" : "green"}
          meterPct={yield_curve < 0 ? curvePct : 15}
        />
        <MacroRow
          label="Financial Conditions"
          value={financial_conditions.toFixed(2)}
          note="NFCI · + = tighter · − = looser"
          color={financial_conditions > 0.75 ? "red" : financial_conditions > 0.25 ? "orange" : financial_conditions < -0.5 ? "green" : "yellow"}
          meterPct={financial_conditions > 0 ? nfciPct : 0}
        />
      </CardBody>
    </Card>
  );
}
