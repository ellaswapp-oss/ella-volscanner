import { clsx } from "clsx";

// ---------------------------------------------------------------------------
// Base card container
// ---------------------------------------------------------------------------
export function Card({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "rounded-xl border border-border bg-surface-2 overflow-hidden",
        className
      )}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card header — title bar with optional right-side action slot
// ---------------------------------------------------------------------------
export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between border-b border-border px-4 py-3">
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
          {title}
        </h3>
        {subtitle && (
          <p className="mt-0.5 text-[10px] text-slate-600">{subtitle}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card body — padded content area
// ---------------------------------------------------------------------------
export function CardBody({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("p-4", className)}>{children}</div>
  );
}

// ---------------------------------------------------------------------------
// Legacy alias — keeps existing panels working without changes
// ---------------------------------------------------------------------------
export function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-400">
      {children}
    </h3>
  );
}
