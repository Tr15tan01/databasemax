"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Activity, BarChart3, Bot, ShieldCheck, CreditCard, Database, DatabaseBackup, FolderKanban, KeyRound, LayoutDashboard, LifeBuoy, Menu, Plus, Settings, Shield, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type { Me } from "@/lib/types";
import { cn } from "@/lib/utils";

import { NotificationsMenu } from "./notifications-menu";
import { UserMenu } from "./user-menu";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/projects", label: "Projects", icon: FolderKanban },
  { href: "/databases", label: "Databases", icon: Database },
  { href: "/backups", label: "Backups", icon: DatabaseBackup },
  { href: "/usage", label: "Usage", icon: BarChart3 },
  { href: "/billing", label: "Billing", icon: CreditCard },
  { href: "/api-keys", label: "API keys", icon: KeyRound },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/approvals", label: "Approvals", icon: ShieldCheck },
  { href: "/activity", label: "Activity", icon: Activity },
];
const NAV_BOTTOM = [
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/support", label: "Support", icon: LifeBuoy },
];

function NavLinks({ me, onNavigate }: { me: Me; onNavigate?: () => void }) {
  const pathname = usePathname() ?? "";
  const pending = useQuery({
    queryKey: ["approvals", "count"],
    queryFn: () => api<{ pending: number }>("/approvals/count"),
    refetchInterval: 30_000,
  });
  const badge = (href: string) =>
    href === "/approvals" && pending.data && pending.data.pending > 0 ? (
      <span className="ml-auto rounded-full bg-warning-soft px-1.5 text-[11px] font-semibold leading-[18px] text-warning tabular" aria-label={`${pending.data.pending} pending`}>
        {pending.data.pending}
      </span>
    ) : null;
  const item = (n: (typeof NAV)[number]) => {
    const active = pathname === n.href || pathname.startsWith(n.href + "/");
    return (
      <Link
        key={n.href}
        href={n.href}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] text-muted transition-colors hover:bg-surface-2 hover:text-foreground",
          active && "bg-surface-2 font-medium text-foreground",
        )}
      >
        <n.icon className="size-4" aria-hidden />
        {n.label}
        {badge(n.href)}
      </Link>
    );
  };
  return (
    <div className="flex h-full flex-col">
      <nav className="space-y-0.5" aria-label="Application">{NAV.map(item)}</nav>
      <div className="mt-auto space-y-0.5 pt-6">
        {NAV_BOTTOM.map(item)}
        {me.is_admin && item({ href: "/admin", label: "Admin", icon: Shield })}
      </div>
    </div>
  );
}

function VerifyBanner() {
  const resend = useMutation({
    mutationFn: () => api("/auth/resend-verification", { method: "POST" }),
    onSuccess: () => toast.success("Verification email sent", { description: "Check your inbox for the link." }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div role="status" className="border-b border-warning/30 bg-warning-soft px-5 py-2 text-[13px] text-foreground">
      Verify your email address to create databases.{" "}
      <button type="button" onClick={() => resend.mutate()} disabled={resend.isPending} className="font-medium underline underline-offset-2">
        Resend verification email
      </button>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { data: me, isLoading, isError } = useMe();
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const [mobileOpen, setMobileOpen] = React.useState(false);

  React.useEffect(() => {
    if (!isLoading && me === null) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [isLoading, me, router, pathname]);

  if (isLoading || !me) {
    return (
      <div className="grid min-h-dvh place-items-center" aria-busy="true">
        {isError ? <p className="text-sm text-muted">Couldn&apos;t reach DatabaseMax. Retrying…</p> : <Skeleton className="h-8 w-40" />}
      </div>
    );
  }

  if (pathname.startsWith("/onboarding")) return <>{children}</>;

  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r border-border bg-surface/60 px-3 py-4 lg:flex">
        <div className="mb-6 px-1.5"><Logo href="/dashboard" /></div>
        <NavLinks me={me} />
      </aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <div className="relative flex h-full w-72 flex-col bg-surface px-3 py-4 shadow-xl">
            <div className="mb-6 flex items-center justify-between px-1.5">
              <Logo href="/dashboard" />
              <button type="button" aria-label="Close navigation" onClick={() => setMobileOpen(false)} className="rounded-md p-1.5 hover:bg-surface-2"><X className="size-4" /></button>
            </div>
            <NavLinks me={me} onNavigate={() => setMobileOpen(false)} />
          </div>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border bg-background/85 px-4 backdrop-blur-md sm:px-6">
          <button type="button" className="rounded-md p-2 text-muted hover:bg-surface-2 lg:hidden" aria-label="Open navigation" onClick={() => setMobileOpen(true)}>
            <Menu className="size-4" />
          </button>
          <div className="hidden text-[13px] text-muted lg:block">
            <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-xs">{me.plan.name} plan</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Button size="sm" asChild className="mr-1">
              <Link href="/databases/new"><Plus /> New database</Link>
            </Button>
            <NotificationsMenu />
            <UserMenu me={me} />
          </div>
        </header>
        {!me.email_verified && <VerifyBanner />}
        <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 lg:py-10">{children}</main>
      </div>
    </div>
  );
}
