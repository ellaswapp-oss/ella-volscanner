"use client";

// ---------------------------------------------------------------------------
// ScoreGauge — semi-circle arc gauge, 0-100
//
// Colour zones in background track match regime thresholds:
//   0-30  green   (Buy Premium)
//   30-50 yellow  (Neutral)
//   50-70 orange  (Sell Selective)
//   70-100 red    (Sell Aggressive)
// ---------------------------------------------------------------------------

function scoreColor(score: number): string {
  if (score < 30) return "#22c55e";
  if (score < 50) return "#eab308";
  if (score < 70) return "#f97316";
  return "#ef4444";
}

// Convert a 0-100 score to an angle along the half-circle (0° = left, 180° = right)
function scoreToAngle(score: number): number {
  return (score / 100) * Math.PI; // radians along the arc
}

// Get the XY position on the arc at a given angle
function arcPoint(cx: number, cy: number, r: number, angle: number) {
  return {
    x: cx - r * Math.cos(angle),
    y: cy - r * Math.sin(angle),
  };
}

export function ScoreGauge({ score, size = 80 }: { score: number; size?: number }) {
  const strokeWidth = Math.max(size * 0.1, 7);
  const r = (size - strokeWidth) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const color = scoreColor(score);

  // Circumference of the half circle
  const circumference = Math.PI * r;
  const dashOffset = circumference * (1 - score / 100);

  // Threshold tick positions (at 30, 50, 70)
  const ticks = [30, 50, 70];
  const tickLen = strokeWidth * 0.7;

  return (
    <svg
      width={size}
      height={size / 2 + strokeWidth / 2 + 2}
      viewBox={`0 0 ${size} ${size / 2 + strokeWidth / 2 + 2}`}
      aria-label={`Regime score: ${score}`}
    >
      {/* Track — segmented zone colours at low opacity */}
      {[
        { start: 0,   end: 30,  color: "#22c55e22" },
        { start: 30,  end: 50,  color: "#eab30822" },
        { start: 50,  end: 70,  color: "#f9731622" },
        { start: 70,  end: 100, color: "#ef444422" },
      ].map(({ start, end, color: zc }) => {
        const aStart = scoreToAngle(start);
        const aEnd   = scoreToAngle(end);
        const p1 = arcPoint(cx, cy, r, aStart);
        const p2 = arcPoint(cx, cy, r, aEnd);
        const large = end - start > 50 ? 1 : 0;
        return (
          <path
            key={start}
            d={`M ${p1.x} ${p1.y} A ${r} ${r} 0 ${large} 1 ${p2.x} ${p2.y}`}
            fill="none"
            stroke={zc}
            strokeWidth={strokeWidth}
            strokeLinecap="butt"
          />
        );
      })}

      {/* Full track outline */}
      <path
        d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
        fill="none"
        stroke="#2d3148"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
      />

      {/* Filled arc */}
      <path
        d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={dashOffset}
        style={{ transition: "stroke-dashoffset 0.7s ease, stroke 0.4s ease" }}
      />

      {/* Threshold tick marks */}
      {ticks.map((t) => {
        const angle = scoreToAngle(t);
        const outer = arcPoint(cx, cy, r + strokeWidth / 2, angle);
        const inner = arcPoint(cx, cy, r - strokeWidth / 2 - tickLen, angle);
        return (
          <line
            key={t}
            x1={outer.x} y1={outer.y}
            x2={inner.x} y2={inner.y}
            stroke="#1a1d2e"
            strokeWidth={1.5}
          />
        );
      })}

      {/* Score label */}
      <text
        x={cx}
        y={cy + (size <= 60 ? 2 : 4)}
        textAnchor="middle"
        fontSize={size * 0.21}
        fill={color}
        fontWeight="bold"
        fontFamily="'JetBrains Mono', 'Fira Code', monospace"
        style={{ transition: "fill 0.4s ease" }}
      >
        {Math.round(score)}
      </text>
    </svg>
  );
}
