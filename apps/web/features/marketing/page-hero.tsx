export function PageHero({ eyebrow, title, description }: { eyebrow?: string; title: string; description?: string }) {
  return (
    <section className="relative overflow-hidden border-b border-border">
      <div aria-hidden className="hairline-grid pointer-events-none absolute inset-0 -z-10 opacity-50" />
      <div className="mx-auto max-w-6xl px-5 pb-14 pt-20">
        {eyebrow && <p className="mb-3 text-[13px] font-medium text-accent">{eyebrow}</p>}
        <h1 className="max-w-3xl text-balance text-4xl font-semibold tracking-[-0.03em] md:text-5xl">{title}</h1>
        {description && <p className="mt-5 max-w-2xl text-[16px] leading-relaxed text-muted">{description}</p>}
      </div>
    </section>
  );
}

export function Prose({ children }: { children: React.ReactNode }) {
  return <div className="prose-docs mx-auto max-w-3xl px-5 py-14">{children}</div>;
}
