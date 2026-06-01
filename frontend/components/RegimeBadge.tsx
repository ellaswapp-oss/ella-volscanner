import { clsx } from "clsx";
import type { Signal } from "@/lib/types";

const colorMap = {
  green: "bg-green-500/20 text-green-400 border-green-500/30",
  yellow: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  orange: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  red: "bg-red-500/20 text-red-400 border-red-500/30",
};

export function RegimeBadge({ signal }: { signal: Signal }) {
  return (
    <span
      className={clsx(
        "rounded-md border px-2 py-1 text-xs font-semibold",
        colorMap[signal.color]
      )}
    >
      {signal.label}
    </span>
  );
}
