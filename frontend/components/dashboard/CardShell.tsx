import { clsx } from "clsx";

interface CardShellProps {
  children: React.ReactNode;
  className?: string;
  accent?: string; // border-l color class e.g. "border-l-green-400"
}

export function CardShell({ children, className, accent }: CardShellProps) {
  return (
    <div
      className={clsx(
        "rounded-xl border border-border bg-surface-2 p-4",
        accent && `border-l-2 ${accent}`,
        className
      )}
    >
      {children}
    </div>
  );
}

/** Uniform skeleton pulse bar */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={clsx("animate-pulse rounded bg-surface-3", className)} />
  );
}

const ROW_WIDTHS = ["w-3/5", "w-4/5", "w-2/3", "w-3/4", "w-1/2"];

/** Full card skeleton for loading state */
export function CardSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <CardShell>
      <Skeleton className="h-3 w-24 mb-4" />
      <Skeleton className="h-8 w-20 mb-3" />
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton
          key={i}
          className={clsx("h-2.5 mb-2", ROW_WIDTHS[i % ROW_WIDTHS.length])}
        />
      ))}
    </CardShell>
  );
}

/** Inline error state for a card */
export function CardError({ message }: { message: string }) {
  return (
    <CardShell className="border-red-500/30">
      <p className="text-xs font-semibold text-red-400 mb-1">Failed to load</p>
      <p className="font-mono text-[10px] text-red-300/60 break-all">{message}</p>
    </CardShell>
  );
}
