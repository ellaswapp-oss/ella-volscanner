// Shown by Next.js during the brief initial HTML shell load.
// Real per-card loading skeletons are handled inside the client component.
export default function Loading() {
  const Pulse = ({ w, h = "h-3" }: { w: string; h?: string }) => (
    <div className={`animate-pulse rounded bg-surface-3 ${h} ${w}`} />
  );

  return (
    <main className="min-h-screen bg-surface px-4 py-5 md:px-6 md:py-7">
      {/* Header */}
      <div className="mb-5 flex items-center justify-between">
        <div className="space-y-2">
          <Pulse w="w-36" h="h-5" />
          <Pulse w="w-48" />
        </div>
        <Pulse w="w-48" h="h-8" />
      </div>

      {/* Row 1 — 2 col */}
      <div className="grid gap-4 grid-cols-1 lg:grid-cols-2 mb-4">
        {[0, 1].map((i) => (
          <div key={i} className="rounded-xl border border-border bg-surface-2 p-4 space-y-3">
            <Pulse w="w-32" />
            <Pulse w="w-20" h="h-10" />
            {[70, 55, 85, 60].map((w, j) => <Pulse key={j} w={`w-[${w}%]`} />)}
          </div>
        ))}
      </div>

      {/* Row 2 — 4 col */}
      <div className="grid gap-4 grid-cols-2 lg:grid-cols-4 mb-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="rounded-xl border border-border bg-surface-2 p-4 space-y-2">
            <Pulse w="w-20" />
            <Pulse w="w-16" h="h-8" />
            <Pulse w="w-full" />
          </div>
        ))}
      </div>

      {/* Row 3 — full */}
      <div className="rounded-xl border border-border bg-surface-2 p-4 space-y-3">
        <Pulse w="w-40" />
        <div className="grid grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => <Pulse key={i} w="w-full" h="h-12" />)}
        </div>
      </div>
    </main>
  );
}
