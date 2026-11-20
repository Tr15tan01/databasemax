import { LifeBuoy, Mail, ShieldAlert } from "lucide-react";
import type { Metadata } from "next";

import { PageHero } from "@/features/marketing/page-hero";

export const metadata: Metadata = { title: "Contact" };

const CHANNELS = [
  { icon: LifeBuoy, title: "Support", body: "Questions about your account or databases. Include the request ID from any error message.", email: "support@databasemax.dev" },
  { icon: Mail, title: "Sales & billing", body: "Plans, invoices and larger deployments.", email: "hello@databasemax.dev" },
  { icon: ShieldAlert, title: "Security", body: "Report a vulnerability. We acknowledge within two business days.", email: "security@databasemax.dev" },
];

export default function ContactPage() {
  return (
    <>
      <PageHero eyebrow="Contact" title="Talk to a human." description="We read every message. Signed-in customers can also reach us from the Support page in the dashboard." />
      <div className="mx-auto grid max-w-6xl gap-4 px-5 py-16 md:grid-cols-3">
        {CHANNELS.map((c) => (
          <a key={c.title} href={`mailto:${c.email}`} className="group rounded-xl border border-border bg-surface p-6 transition-colors hover:border-border-strong">
            <c.icon className="size-5 text-muted" aria-hidden />
            <h2 className="mt-4 font-semibold">{c.title}</h2>
            <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{c.body}</p>
            <p className="mt-4 text-[13px] font-medium text-accent group-hover:underline">{c.email}</p>
          </a>
        ))}
      </div>
    </>
  );
}
