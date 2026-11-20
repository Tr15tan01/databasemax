"use client";
import { Activity, ArrowLeft, CreditCard, Database, DatabaseBackup, LayoutDashboard, ListChecks, Network, ScrollText, Server, ShieldAlert, Users } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { LogoMark } from "@/components/logo";
import { Skeleton } from "@/components/ui/skeleton";
import { UserMenu } from "@/features/app/user-menu";
import { useMe } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/admin", label: "Overview", icon: LayoutDashboard, exact: true },
  { href: "/admin/users", label: "Users", icon: Users },
  { href: "/admin/databases", label: "Databases", icon: Database },
  { href: "/admin/jobs", label: "Provisioning queue", icon: ListChecks },
  { href: "/admin/backups", label: "Backups", icon: DatabaseBackup },
  { href: "/admin/infrastructure", label: "Infrastructure", icon: Server },
  { href: "/admin/hosts", label: "Hosts", icon: Network },
  { href: "/admin/security", label: "Security", icon: ShieldAlert },
  { href: "/admin/billing", label: "Billing", icon: CreditCard },
  { href: "/admin/audit", label: "Audit log", icon: ScrollText },
];

export function AdminShell({ children }: { children: React.ReactNode }) {
  const { data: me, isLoading } = useMe();
  const router = useRouter();
  const pathname = usePathname() ?? "";
  React.useEffect(() => {
    if (!isLoading && me === null) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [isLoading, me, router, pathname]);
  if (isLoading || !me) return <div className="grid min-h-dvh place-items-center"><Skeleton className="h-8 w-40" /></div>;
  if (!me.is_admin)
    return (
      <div className="grid min-h-dvh place-items-center text-center">
        <div><h1 className="text-xl font-semibold">Page not found</h1><Link href="/dashboard" className="mt-3 inline-block text-sm text-accent">Back to dashboard</Link></div>
      </div>
    );
  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r border-border bg-surface px-3 py-4 lg:flex">
        <div className="mb-6 flex items-center gap-2 px-1.5 font-semibold">
          <LogoMark /> <span className="text-[15px]">DatabaseMax</span>
          <span className="rounded-md bg-critical-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-critical">Admin</span>
        </div>
        <nav className="space-y-0.5" aria-label="Admin">
          {NAV.map((n) => {
            const active = n.exact ? pathname === n.href : pathname.startsWith(n.href);
            return (
              <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
                className={cn("flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] text-muted hover:bg-surface-2 hover:text-foreground", active && "bg-surface-2 font-medium text-foreground")}>
                <n.icon className="size-4" /> {n.label}
              </Link>
            );
          })}
        </nav>
        <Link href="/dashboard" className="mt-auto flex items-center gap-2 px-2.5 py-1.5 text-[13px] text-muted hover:text-foreground"><ArrowLeft className="size-4" /> Back to app</Link>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border bg-background/85 px-6 backdrop-blur-md">
          <nav className="flex gap-1 overflow-x-auto lg:hidden" aria-label="Admin mobile">
            {NAV.map((n) => <Link key={n.href} href={n.href} className="whitespace-nowrap rounded-md px-2 py-1 text-xs text-muted">{n.label}</Link>)}
          </nav>
          <div className="hidden items-center gap-2 text-[13px] text-muted lg:flex"><Activity className="size-4" /> Operator console — all actions are audited</div>
          <UserMenu me={me} />
        </header>
        <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">{children}</main>
      </div>
    </div>
  );
}
