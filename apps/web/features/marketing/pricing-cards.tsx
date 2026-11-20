import { Check } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import type { Plan } from "@/lib/types";
import { cn, formatCents } from "@/lib/utils";

export function PricingCards({ plans, compact = false }: { plans: Plan[] | null; compact?: boolean }) {
  if (!plans?.length) {
    return (
      <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted">
        Pricing is temporarily unavailable. Please check back shortly.
      </p>
    );
  }
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {plans.map((p) => {
        const featured = p.id === "developer";
        return (
          <div
            key={p.id}
            className={cn(
              "relative flex flex-col rounded-xl border bg-surface p-6",
              featured ? "border-foreground/80 shadow-[var(--shadow-pop)]" : "border-border",
            )}
          >
            {featured && (
              <span className="absolute -top-2.5 left-6 rounded-full bg-primary px-2.5 py-0.5 text-[11px] font-medium text-primary-foreground">
                Most popular
              </span>
            )}
            <h3 className="font-semibold">{p.name}</h3>
            <p className="mt-1 text-[13px] text-muted">{p.description}</p>
            <div className="mt-5 flex items-baseline gap-1">
              <span className="text-4xl font-semibold tracking-[-0.03em]">{formatCents(p.price_cents)}</span>
              <span className="text-sm text-subtle">/month</span>
            </div>
            <Button className="mt-6" variant={featured ? "primary" : "secondary"} asChild>
              <Link href={`/register?plan=${p.id}`}>{p.price_cents === 0 ? "Start for free" : `Choose ${p.name}`}</Link>
            </Button>
            <ul className={cn("mt-6 space-y-2.5 text-[13px]", compact && "space-y-2")}>
              {(compact ? p.features.slice(0, 6) : p.features).map((f) => (
                <li key={f} className="flex gap-2.5 text-muted">
                  <Check className="mt-0.5 size-4 shrink-0 text-foreground" aria-hidden />
                  {f}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
