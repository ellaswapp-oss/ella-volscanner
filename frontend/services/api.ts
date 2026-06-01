/**
 * services/api.ts
 * ---------------
 * Thin re-export shim.  The canonical API client lives at lib/api.ts.
 * This file exists so existing imports of "@/services/api" continue to work.
 *
 * Prefer importing directly from "@/lib/api" in new code.
 */
export { api, dashboard, ticker, macro, tickers } from "@/lib/api";
