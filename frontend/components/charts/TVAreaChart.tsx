"use client";

/**
 * TVAreaChart — TradingView Lightweight Charts v5 wrapper.
 *
 * Renders an area series (IV30) overlaid with a line series (RV30).
 * Dynamically imported inside useEffect so no SSR issues.
 *
 * Phase 3: swap `iv` / `rv` props for live ORATS + Polygon time series.
 */

import { useEffect, useRef } from "react";

export interface TVDataPoint {
  time: string;   // "YYYY-MM-DD"
  value: number;  // vol level (%)
}

interface Props {
  iv: TVDataPoint[];
  rv: TVDataPoint[];
  height?: number;
  /** Show a horizontal reference line at this IV level */
  refLine?: number;
}

const THEME = {
  bg:          "transparent",
  text:        "#64748b",
  grid:        "#1e2235",
  border:      "#2d3148",
  crosshair:   "#60a5fa55",
  labelBg:     "#1a1d2e",
  iv:          "#60a5fa",
  ivTop:       "rgba(96,165,250,0.18)",
  ivBottom:    "rgba(96,165,250,0)",
  rv:          "#f97316",
};

export function TVAreaChart({ iv, rv, height = 180, refLine }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || iv.length === 0) return;

    // Guard against React 18 Strict Mode double-invoke:
    // if cleanup fires before the async import resolves, skip chart creation.
    let cancelled = false;
    let removeChart: (() => void) | undefined;

    // Dynamic import avoids SSR "window is not defined"
    import("lightweight-charts").then((lc) => {
      if (cancelled || !containerRef.current) return;

      const chart = lc.createChart(containerRef.current, {
        autoSize: true,
        layout: {
          background: { type: lc.ColorType.Solid, color: THEME.bg },
          textColor: THEME.text,
          fontSize: 11,
        },
        grid: {
          vertLines: { color: THEME.grid },
          horzLines: { color: THEME.grid },
        },
        crosshair: {
          mode: lc.CrosshairMode.Normal,
          vertLine: { color: THEME.crosshair, labelBackgroundColor: THEME.labelBg },
          horzLine: { color: THEME.crosshair, labelBackgroundColor: THEME.labelBg },
        },
        rightPriceScale: {
          borderColor: THEME.border,
          scaleMargins: { top: 0.1, bottom: 0.08 },
        },
        timeScale: {
          borderColor: THEME.border,
          timeVisible: false,
          fixLeftEdge: true,
          fixRightEdge: true,
        },
        handleScroll: false,
        handleScale: false,
      });

      // IV30 — filled area
      const ivSeries = chart.addSeries(lc.AreaSeries, {
        lineColor:   THEME.iv,
        topColor:    THEME.ivTop,
        bottomColor: THEME.ivBottom,
        lineWidth:   2,
        title:       "IV30",
        priceFormat: { type: "custom", formatter: (v: number) => v.toFixed(1) + "%" },
      });

      // RV30 — line only
      const rvSeries = chart.addSeries(lc.LineSeries, {
        color:     THEME.rv,
        lineWidth: 2,
        title:     "RV30",
        priceFormat: { type: "custom", formatter: (v: number) => v.toFixed(1) + "%" },
      });

      ivSeries.setData(iv);
      rvSeries.setData(rv);

      // Optional horizontal reference line (e.g., long-run mean IV)
      if (refLine !== undefined) {
        ivSeries.createPriceLine({
          price: refLine,
          color: "#64748b",
          lineWidth: 1,
          lineStyle: lc.LineStyle.Dashed,
          axisLabelVisible: false,
          title: "mean",
        });
      }

      chart.timeScale().fitContent();

      removeChart = () => chart.remove();
    });

    return () => {
      cancelled = true;
      removeChart?.();
    };
  }, [iv, rv, refLine, height]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height }}
      aria-label="IV vs RV chart"
    />
  );
}
