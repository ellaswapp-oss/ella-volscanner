"use client";

import { useState } from "react";
import { clsx } from "clsx";

interface DataCardProps {
  title: string;
  endpoint: string;
  result: PromiseSettledResult<unknown>;
  defaultOpen?: boolean;
}

// ---------------------------------------------------------------------------
// Minimal JSON syntax highlighter — runs client-side only
// ---------------------------------------------------------------------------
function highlight(json: string): string {
  return json
    // strings (keys and values)
    .replace(
      /("(?:[^"\\]|\\.)*")\s*:/g,
      '<span class="text-sky-400">$1</span>:'
    )
    .replace(
      /:\s*("(?:[^"\\]|\\.)*")/g,
      ': <span class="text-emerald-400">$1</span>'
    )
    // numbers
    .replace(
      /:\s*(-?\d+\.?\d*(?:[eE][+-]?\d+)?)/g,
      ': <span class="text-amber-300">$1</span>'
    )
    // booleans + null
    .replace(
      /:\s*(true|false|null)/g,
      ': <span class="text-rose-400">$1</span>'
    );
}

// ---------------------------------------------------------------------------
// DataCard
// ---------------------------------------------------------------------------
export function DataCard({
  title,
  endpoint,
  result,
  defaultOpen = true,
}: DataCardProps) {
  const ok = result.status === "fulfilled";
  const [open, setOpen] = useState(defaultOpen);

  const json = ok
    ? JSON.stringify((result as PromiseFulfilledResult<unknown>).value, null, 2)
    : null;

  const errorMsg = !ok
    ? String((result as PromiseRejectedResult).reason)
    : null;

  // Key stats pulled from top level for quick scan
  const data = ok ? (result as PromiseFulfilledResult<Record<string, unknown>>).value as Record<string, unknown> : null;

  return (
    <div
      className={clsx(
        "rounded-xl border bg-surface-2 overflow-hidden",
        ok ? "border-border" : "border-red-500/40"
      )}
    >
      {/* ------------------------------------------------------------------ */}
      {/* Header                                                               */}
      {/* ------------------------------------------------------------------ */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-start justify-between gap-3 px-4 py-3 text-left hover:bg-surface-3/40 transition-colors"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-white">{title}</span>
            <span
              className={clsx(
                "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
                ok
                  ? "bg-green-500/10 text-green-400 border border-green-500/20"
                  : "bg-red-500/10 text-red-400 border border-red-500/20"
              )}
            >
              <span
                className={clsx(
                  "inline-block h-1.5 w-1.5 rounded-full",
                  ok ? "bg-green-400" : "bg-red-400"
                )}
              />
              {ok ? "200 OK" : "Error"}
            </span>
          </div>
          <span className="mt-0.5 block font-mono text-[10px] text-slate-500">
            GET {endpoint}
          </span>
        </div>
        <span className="mt-0.5 shrink-0 text-slate-500 text-xs">
          {open ? "▲" : "▼"}
        </span>
      </button>

      {/* ------------------------------------------------------------------ */}
      {/* Quick-scan stat pills — fulfilled responses only                    */}
      {/* ------------------------------------------------------------------ */}
      {ok && data && (
        <div className="flex flex-wrap gap-1.5 px-4 pb-2">
          {Object.entries(data)
            .filter(([, v]) => typeof v === "number" || typeof v === "string")
            .slice(0, 6)
            .map(([k, v]) => (
              <span
                key={k}
                className="rounded-md bg-surface-3 px-2 py-0.5 font-mono text-[10px] text-slate-400"
              >
                <span className="text-slate-500">{k}:</span>{" "}
                <span className="text-white">
                  {typeof v === "number" ? v.toLocaleString() : String(v)}
                </span>
              </span>
            ))}
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Error state                                                          */}
      {/* ------------------------------------------------------------------ */}
      {!ok && (
        <div className="mx-4 mb-3 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2">
          <p className="text-xs font-semibold text-red-400">Fetch failed</p>
          <p className="mt-0.5 font-mono text-[10px] text-red-300/70 break-all">
            {errorMsg}
          </p>
          <p className="mt-1.5 text-[10px] text-slate-500">
            Is the backend running?{" "}
            <code className="rounded bg-surface-3 px-1 text-slate-400">
              uvicorn app.main:app --reload --port 8000
            </code>
          </p>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* JSON body                                                            */}
      {/* ------------------------------------------------------------------ */}
      {ok && open && json && (
        <div className="border-t border-border mx-0">
          <pre
            className="overflow-x-auto p-4 text-[11px] leading-relaxed text-slate-300 font-mono"
            dangerouslySetInnerHTML={{ __html: highlight(json) }}
          />
        </div>
      )}
    </div>
  );
}
