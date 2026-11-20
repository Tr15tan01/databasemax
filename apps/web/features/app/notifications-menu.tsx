"use client";
import * as Popover from "@radix-ui/react-popover";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, CheckCheck } from "lucide-react";
import Link from "next/link";

import { api } from "@/lib/api";
import type { Notification, Page } from "@/lib/types";
import { cn, relativeTime } from "@/lib/utils";

const DOT: Record<string, string> = { error: "bg-critical", warning: "bg-warning", info: "bg-accent" };

export function NotificationsMenu() {
  const qc = useQueryClient();
  const count = useQuery({ queryKey: ["notifications", "count"], queryFn: () => api<{ unread: number }>("/notifications/unread-count"), refetchInterval: 30_000 });
  const list = useQuery({ queryKey: ["notifications", "list"], queryFn: () => api<Page<Notification>>("/notifications", { query: { page_size: 8 } }) });
  const readAll = useMutation({
    mutationFn: () => api("/notifications/read-all", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const read = useMutation({
    mutationFn: (id: string) => api(`/notifications/${id}/read`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const unread = count.data?.unread ?? 0;
  return (
    <Popover.Root onOpenChange={(o) => o && list.refetch()}>
      <Popover.Trigger
        className="relative grid size-9 place-items-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-foreground"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
      >
        <Bell className="size-4" />
        {unread > 0 && (
          <span className="absolute right-1.5 top-1.5 grid min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] font-semibold leading-4 text-accent-foreground">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={8} className="z-50 w-[360px] max-w-[calc(100vw-1rem)] rounded-xl border border-border bg-surface shadow-[var(--shadow-pop)] data-[state=open]:animate-pop-in">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <span className="text-sm font-semibold">Notifications</span>
            <button type="button" className="flex items-center gap-1 text-xs text-muted hover:text-foreground disabled:opacity-40" disabled={!unread} onClick={() => readAll.mutate()}>
              <CheckCheck className="size-3.5" /> Mark all read
            </button>
          </div>
          <ul className="max-h-[420px] overflow-y-auto">
            {list.data?.items.length === 0 && <li className="px-4 py-10 text-center text-[13px] text-subtle">You&apos;re all caught up.</li>}
            {list.data?.items.map((n) => {
              const body = (
                <div className="flex gap-3 px-4 py-3">
                  <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", n.read_at ? "bg-transparent" : DOT[n.severity] ?? "bg-accent")} aria-hidden />
                  <div className="min-w-0">
                    <p className={cn("text-[13px]", !n.read_at && "font-medium")}>{n.title}</p>
                    {n.body && <p className="mt-0.5 line-clamp-2 text-xs text-muted">{n.body}</p>}
                    <p className="mt-1 text-[11px] text-subtle">{relativeTime(n.created_at)}</p>
                  </div>
                </div>
              );
              return (
                <li key={n.id} className="border-b border-border last:border-0 hover:bg-surface-2/60">
                  {n.link ? (
                    <Popover.Close asChild>
                      <Link href={n.link} onClick={() => !n.read_at && read.mutate(n.id)} className="block">{body}</Link>
                    </Popover.Close>
                  ) : (
                    <button type="button" className="block w-full text-left" onClick={() => !n.read_at && read.mutate(n.id)}>{body}</button>
                  )}
                </li>
              );
            })}
          </ul>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
