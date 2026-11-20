"use client";

import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { CopyButton } from "@/components/ui/copy-button";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { IN_FLIGHT, StatusBadge } from "@/components/ui/status-badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConnectionDetails } from "@/features/databases/connection-details";
import { ProvisioningProgress } from "@/features/databases/provisioning-progress";
import { ActivityTab } from "@/features/databases/tabs/activity";
import { BackupsTab } from "@/features/databases/tabs/backups";
import { InsightsTab } from "@/features/databases/tabs/insights";
import { LiveTab } from "@/features/databases/tabs/live";
import { OverviewTab } from "@/features/databases/tabs/overview";
import { SettingsTab } from "@/features/databases/tabs/settings";
import { SqlTab } from "@/features/databases/tabs/sql";
import { UsageTab } from "@/features/databases/tabs/usage";
import { useDatabase } from "@/features/databases/use-database";
import { ENV_LABELS } from "@/lib/labels";

const TABS = ["overview", "connection", "sql", "live", "insights", "usage", "settings", "backups", "activity"] as const;
const TAB_LABELS: Record<(typeof TABS)[number], string> = {
  overview: "Overview",
  connection: "Connection",
  sql: "SQL editor",
  live: "Live",
  insights: "Insights",
  usage: "Usage",
  settings: "Settings",
  backups: "Backups",
  activity: "Activity",
};

function Inner() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const tab = (TABS as readonly string[]).includes(params.get("tab") ?? "") ? params.get("tab")! : "overview";
  const q = useDatabase(id);
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} title="Couldn't load this database" />;
  const d = q.data;
  if (!d) return <Skeleton className="h-60" />;
  const provisioning = IN_FLIGHT.has(d.status) && !["stopping", "deleting", "migrating"].includes(d.status);
  return (
    <>
      <PageHeader
        eyebrow={<span><Link href="/databases" className="hover:text-foreground">Databases</Link> / <Link href={`/projects/${d.project_id}`} className="hover:text-foreground">{d.project_name}</Link></span>}
        title={<span className="flex flex-wrap items-center gap-3">{d.name} <StatusBadge status={d.status} /> <Badge variant="outline">{ENV_LABELS[d.environment]}</Badge></span>}
        description={
          <span className="flex items-center gap-1 font-mono text-xs">
            {d.host}:{d.port}/{d.database_name}
            <CopyButton value={`${d.host}`} label="Copy host" />
          </span>
        }
      />
      {d.host_status === "unavailable" && (
        <div role="alert" className="mb-6 rounded-lg border border-critical/30 bg-critical-soft px-4 py-3 text-[13px] text-critical">
          The server running this database is unreachable, so connections fail right now. Our operators have been alerted; your
          data stays on its volume and the database will not be moved without notice.
        </div>
      )}
      {d.status === "migrating" && (
        <div role="status" className="mb-6 rounded-lg border border-warning/30 bg-warning-soft px-4 py-3 text-[13px]">
          This database is being moved to another server. It is read-only, then briefly unavailable; the connection URL and
          password stay the same.
        </div>
      )}
      {(provisioning || d.status === "failed") && (
        <Card className="mb-6 p-6"><ProvisioningProgress database={d} /></Card>
      )}
      <Tabs value={tab} onValueChange={(v) => router.replace(`${pathname}?tab=${v}`, { scroll: false })}>
        <TabsList>
          {TABS.map((t) => <TabsTrigger key={t} value={t}>{TAB_LABELS[t]}</TabsTrigger>)}
        </TabsList>
        <TabsContent value="overview"><OverviewTab database={d} /></TabsContent>
        <TabsContent value="connection"><ConnectionDetails database={d} /></TabsContent>
        <TabsContent value="sql"><SqlTab database={d} /></TabsContent>
        <TabsContent value="live"><LiveTab database={d} /></TabsContent>
        <TabsContent value="insights"><InsightsTab database={d} /></TabsContent>
        <TabsContent value="usage"><UsageTab database={d} /></TabsContent>
        <TabsContent value="settings"><SettingsTab database={d} /></TabsContent>
        <TabsContent value="backups"><BackupsTab database={d} /></TabsContent>
        <TabsContent value="activity"><ActivityTab database={d} /></TabsContent>
      </Tabs>
    </>
  );
}

export default function DatabaseDetailPage() {
  return <React.Suspense><Inner /></React.Suspense>;
}
