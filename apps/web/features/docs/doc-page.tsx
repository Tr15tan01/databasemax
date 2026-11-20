export function DocHeader({ title, lead }: { title: string; lead?: string }) {
  return (
    <header className="mb-8 border-b border-border pb-8">
      <h1 className="text-3xl font-semibold tracking-[-0.025em]">{title}</h1>
      {lead && <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-muted">{lead}</p>}
    </header>
  );
}

export function Callout({ tone = "info", title, children }: { tone?: "info" | "warning"; title: string; children: React.ReactNode }) {
  return (
    <div
      className={
        "my-5 rounded-lg border px-4 py-3 text-[13px] leading-relaxed " +
        (tone === "warning" ? "border-warning/30 bg-warning-soft" : "border-accent/25 bg-accent-soft")
      }
    >
      <p className="!m-0 font-semibold !text-foreground">{title}</p>
      <div className="mt-1 text-muted [&_p]:!m-0">{children}</div>
    </div>
  );
}
