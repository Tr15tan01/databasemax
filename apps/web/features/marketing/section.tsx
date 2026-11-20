import { cn } from "@/lib/utils";

export function Section({ id, eyebrow, title, description, children, className }: {
  id?: string; eyebrow?: string; title: React.ReactNode; description?: React.ReactNode; children?: React.ReactNode; className?: string;
}) {
  return (
    <section id={id} className={cn("mx-auto max-w-6xl scroll-mt-20 px-5 py-20 md:py-24", className)}>
      <div className="max-w-2xl">
        {eyebrow && <p className="mb-3 text-[13px] font-medium text-accent">{eyebrow}</p>}
        <h2 className="text-balance text-3xl font-semibold tracking-[-0.025em] md:text-[34px] md:leading-tight">{title}</h2>
        {description && <p className="mt-4 text-[15px] leading-relaxed text-muted">{description}</p>}
      </div>
      {children && <div className="mt-12">{children}</div>}
    </section>
  );
}

export function FeatureCard({ icon: Icon, title, children }: { icon: React.ElementType; title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-5">
      <div className="mb-4 grid size-9 place-items-center rounded-lg border border-border bg-surface-2">
        <Icon className="size-4 text-foreground" aria-hidden />
      </div>
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{children}</p>
    </div>
  );
}
