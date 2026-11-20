import Link from "next/link";

import { cn } from "@/lib/utils";

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-7", className)} aria-hidden>
      <rect width="32" height="32" rx="8" fill="var(--primary)" />
      <ellipse cx="16" cy="10.5" rx="7.5" ry="3" fill="none" stroke="var(--primary-foreground)" strokeWidth="1.8" />
      <path d="M8.5 10.5v5.5c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3v-5.5" fill="none" stroke="var(--primary-foreground)" strokeWidth="1.8" />
      <path d="M8.5 16v5.5c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3V16" fill="none" stroke="var(--primary-foreground)" strokeWidth="1.8" opacity="0.55" />
      <circle cx="23.5" cy="23.5" r="3.2" fill="var(--accent)" stroke="var(--primary)" strokeWidth="1.5" />
    </svg>
  );
}

export function Logo({ href = "/", className }: { href?: string; className?: string }) {
  return (
    <Link href={href} className={cn("flex items-center gap-2 font-semibold tracking-[-0.02em]", className)} aria-label="DatabaseMax home">
      <LogoMark />
      <span className="text-[15px]">DatabaseMax</span>
    </Link>
  );
}
