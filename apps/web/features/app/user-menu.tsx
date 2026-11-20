"use client";
import { useQueryClient } from "@tanstack/react-query";
import { BookOpen, LogOut, Settings, Shield } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ThemeToggle } from "@/components/theme-toggle";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";

export function initials(me: Pick<Me, "display_name" | "email">) {
  const base = me.display_name || me.email;
  return base.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((s) => s[0]!.toUpperCase()).join("");
}

export function UserMenu({ me }: { me: Me }) {
  const router = useRouter();
  const qc = useQueryClient();
  const signOut = async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      qc.clear();
      router.replace("/login");
    }
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="grid size-8 place-items-center rounded-full border border-border bg-surface-2 text-xs font-semibold" aria-label="Account menu">
        {initials(me)}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel>
          <div className="truncate text-[13px] font-medium text-foreground">{me.display_name || "Signed in"}</div>
          <div className="truncate">{me.email}</div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild><Link href="/settings"><Settings /> Settings</Link></DropdownMenuItem>
        <DropdownMenuItem asChild><Link href="/docs"><BookOpen /> Documentation</Link></DropdownMenuItem>
        {me.is_admin && <DropdownMenuItem asChild><Link href="/admin"><Shield /> Admin</Link></DropdownMenuItem>}
        <DropdownMenuSeparator />
        <div className="flex items-center justify-between px-2 py-1.5 text-[13px] text-muted">
          Theme <ThemeToggle />
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={signOut}><LogOut /> Sign out</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
