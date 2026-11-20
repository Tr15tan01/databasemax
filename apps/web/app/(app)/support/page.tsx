"use client";
import { useQuery } from "@tanstack/react-query";
import { Activity, BookOpen, LifeBuoy, Mail, ShieldAlert } from "lucide-react";
import Link from "next/link";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { api } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type { Quotas } from "@/lib/types";

const LEVELS: Record<string, { title: string; body: string }> = {
  community: { title: "Community support", body: "Use the documentation and email us for account or billing problems. Responses are best-effort." },
  email: { title: "Email support", body: "Email support@databasemax.dev. We aim to reply within two business days." },
  priority: { title: "Priority email support", body: "Email support@databasemax.dev with your account email. Priority requests are answered first, within one business day." },
};

export default function SupportPage() {
  const { data: me } = useMe();
  const q = useQuery({ queryKey: ["usage-quotas"], queryFn: () => api<Quotas>("/usage/quotas") });
  const level = LEVELS[String(q.data?.limits.support ?? "community")] ?? LEVELS.community!;
  const subject = encodeURIComponent(`Support request (${me?.email ?? ""})`);
  return (
    <>
      <PageHeader title="Support" description="Get help with your databases, account or billing." />
      <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
        <Card>
          <CardHeader><div><CardTitle className="flex items-center gap-2"><LifeBuoy className="size-4" /> {level.title}</CardTitle><CardDescription>Included in your {q.data?.plan.name ?? ""} plan.</CardDescription></div></CardHeader>
          <CardContent className="space-y-4 text-[13px] text-muted">
            <p>{level.body}</p>
            <p>To help us resolve issues quickly, include:</p>
            <ul className="list-disc space-y-1 pl-5">
              <li>the database ID (Overview tab) or project name,</li>
              <li>the <strong className="text-foreground">request ID</strong> shown with any error message,</li>
              <li>what you expected and what happened instead.</li>
            </ul>
            <p>Never send us your database password or API keys — we don&apos;t need them.</p>
            <a href={`mailto:support@databasemax.dev?subject=${subject}`} className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground">
              <Mail className="size-4" /> Email support
            </a>
          </CardContent>
        </Card>
        <div className="space-y-4">
          {[
            { href: "/docs/connection#troubleshooting", icon: BookOpen, t: "Connection troubleshooting", d: "Fixes for the most common connection errors." },
            { href: "/status", icon: Activity, t: "Service status", d: "Live status of the API, provisioning and gateway." },
            { href: "mailto:security@databasemax.dev", icon: ShieldAlert, t: "Report a security issue", d: "security@databasemax.dev" },
          ].map((c) => (
            <Link key={c.t} href={c.href} className="flex gap-3 rounded-lg border border-border bg-surface p-4 transition-colors hover:border-border-strong">
              <c.icon className="mt-0.5 size-4 text-muted" />
              <div><div className="text-[13px] font-medium">{c.t}</div><div className="text-xs text-muted">{c.d}</div></div>
            </Link>
          ))}
        </div>
      </div>
    </>
  );
}
