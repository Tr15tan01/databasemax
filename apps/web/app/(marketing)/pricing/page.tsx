import { Check, Minus } from "lucide-react";
import type { Metadata } from "next";

import { PageHero } from "@/features/marketing/page-hero";
import { PricingCards } from "@/features/marketing/pricing-cards";
import { getPlans } from "@/lib/server/api";
import { formatMB } from "@/lib/utils";

export const metadata: Metadata = { title: "Pricing" };
export const dynamic = "force-dynamic";

type Row = [string, (l: Record<string, unknown>) => React.ReactNode];

const yes = <Check className="mx-auto size-4" aria-label="Included" />;
const no = <Minus className="mx-auto size-4 text-subtle" aria-label="Not included" />;

const ROWS: Row[] = [
  ["Projects", (l) => String(l.max_projects)],
  ["PostgreSQL databases", (l) => String(l.max_databases)],
  ["Total storage", (l) => formatMB(Number(l.max_total_storage_mb))],
  ["Connections per database", (l) => String(l.max_connections_per_db)],
  ["Compute sizes", (l) => (l.compute_profiles as string[]).join(", ")],
  ["Credential rotation", (l) => (l.credential_rotation ? yes : no)],
  ["API access", (l) => (l.api_access ? yes : no)],
  ["API keys", (l) => String(l.max_api_keys)],
  ["Usage alerts", (l) => (l.usage_alerts === "none" ? no : String(l.usage_alerts))],
  ["Support", (l) => String(l.support)],
];

export default async function PricingPage() {
  const plans = await getPlans();
  return (
    <>
      <PageHero eyebrow="Pricing" title="Simple plans. Enforced limits. No surprises." description="Flat monthly prices in USD. Taxes are calculated at checkout by Paddle, our payment provider." />
      <div className="mx-auto max-w-6xl px-5 py-16">
        <PricingCards plans={plans} />
        {plans && (
          <div className="mt-16 overflow-x-auto rounded-xl border border-border bg-surface">
            <table className="w-full text-sm">
              <caption className="sr-only">Plan comparison</caption>
              <thead>
                <tr className="border-b border-border">
                  <th className="px-5 py-3 text-left font-medium text-muted">Limits</th>
                  {plans.map((p) => (
                    <th key={p.id} className="px-5 py-3 text-center font-semibold">{p.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ROWS.map(([label, fn]) => (
                  <tr key={label} className="border-b border-border last:border-0">
                    <td className="px-5 py-3 text-muted">{label}</td>
                    {plans.map((p) => (
                      <td key={p.id} className="px-5 py-3 text-center capitalize tabular">{fn(p.limits)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="prose-docs mt-16 grid gap-10 md:grid-cols-2">
          <div>
            <h2>Billing policy</h2>
            <p>Plans renew monthly. Upgrades take effect as soon as Paddle confirms the payment; downgrades and cancellations apply at the end of the billing period.</p>
            <p>If a payment fails, your plan stays active for a <strong>14-day grace period</strong>. We never delete a database because of a failed payment. After the grace period, databases above the Free plan&apos;s limits are <strong>stopped, not deleted</strong>, and you&apos;re notified before anything happens.</p>
          </div>
          <div>
            <h2>Backups, and what&apos;s not included yet</h2>
            <p>Every plan includes daily encrypted backups with the retention shown above and restores into a new database. Point-in-time recovery, high availability and multiple regions are not part of any plan today. We&apos;ll add them only after they&apos;re implemented and tested.</p>
            <p>Storage is measured every few minutes; when a database exceeds its allocation, new sessions become read-only by default until space is freed or the allocation is increased. Each database also sits on its own fixed-size volume, so it can never use more than a small margin above its allocation.</p>
          </div>
        </div>
      </div>
    </>
  );
}
