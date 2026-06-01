"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "@/services/api";
import type { DashboardData } from "@/lib/types";

interface UseVolDataResult {
  data: DashboardData | null;
  loading: boolean;
  error: string | null;
  lastUpdated: Date | null;
  refresh: () => Promise<void>;
}

export function useVolData(refreshIntervalMs = 60_000): UseVolDataResult {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();

    try {
      const result = await api.vol.dashboard();
      setData(result);
      setError(null);
      setLastUpdated(new Date());
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Failed to fetch dashboard data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, refreshIntervalMs);
    return () => {
      clearInterval(id);
      abortRef.current?.abort();
    };
  }, [refresh, refreshIntervalMs]);

  return { data, loading, error, lastUpdated, refresh };
}
