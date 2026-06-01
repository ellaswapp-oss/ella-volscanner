import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export function fmtPct(v: number, decimals = 1): string {
  return `${v.toFixed(decimals)}%`;
}

export function fmtSign(v: number, decimals = 1): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}`;
}

export function fmtPrice(v: number): string {
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
