/**
 * components/ProviderBadge.tsx
 * ----------------------------
 * Async server component — fetches the active data-provider status from the
 * backend and renders a small pill badge in the top-right corner of the layout.
 *
 * Visual states
 * -------------
 *   Mock Data  — amber pill  (DATA_PROVIDER=mock, default)
 *   Live Data  — green pill  (DATA_PROVIDER=real with API keys configured)
 *
 * Falls back silently if the backend is unreachable (returns null).
 */

import { getProviderStatus } from "@/lib/api";

export default async function ProviderBadge() {
  let label: string;
  let isLive: boolean;

  try {
    const status = await getProviderStatus();
    label  = status.label;
    isLive = status.is_live;
  } catch {
    // Backend not available during build or SSR — render nothing rather than crash.
    return null;
  }

  const colorCls = isLive
    ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-inset ring-emerald-500/30"
    : "bg-amber-500/15  text-amber-400  ring-1 ring-inset ring-amber-500/30";

  const dotCls = isLive ? "bg-emerald-400" : "bg-amber-400";

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${colorCls}`}
      title={isLive ? "Using live market data" : "Using synthetic mock data"}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dotCls}`} aria-hidden="true" />
      {label}
    </span>
  );
}
