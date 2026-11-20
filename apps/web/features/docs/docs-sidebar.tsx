"use client";
import { Search } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { cn } from "@/lib/utils";

import { DOCS_NAV } from "./nav";

export function DocsSearch() {
  const [q, setQ] = React.useState("");
  const [active, setActive] = React.useState(0);
  const router = useRouter();
  const all = DOCS_NAV.flatMap((s) => s.links.map((l) => ({ ...l, section: s.section })));
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const results = terms.length
    ? all.filter((l) => terms.every((t) => `${l.title} ${l.keywords ?? ""} ${l.section}`.toLowerCase().includes(t)))
    : [];
  return (
    <div className="relative">
      <label htmlFor="docs-search" className="sr-only">Search documentation</label>
      <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle" aria-hidden />
      <input
        id="docs-search"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, results.length - 1));
          if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          if (e.key === "Enter" && results[active]) {
            router.push(results[active]!.href);
            setQ("");
          }
          if (e.key === "Escape") setQ("");
        }}
        placeholder="Search docs…"
        role="combobox"
        aria-expanded={results.length > 0}
        aria-controls="docs-search-results"
        className="h-9 w-full rounded-md border border-border bg-surface pl-8 pr-3 text-[13px] placeholder:text-subtle"
      />
      {q && (
        <ul id="docs-search-results" role="listbox" className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg border border-border bg-surface shadow-[var(--shadow-pop)]">
          {results.length === 0 && <li className="px-3 py-2 text-[13px] text-subtle">No results</li>}
          {results.map((r, i) => (
            <li key={r.href} role="option" aria-selected={i === active}>
              <Link
                href={r.href}
                onClick={() => setQ("")}
                className={cn("block px-3 py-2 text-[13px]", i === active && "bg-surface-2")}
              >
                <span className="font-medium">{r.title}</span>
                <span className="ml-2 text-xs text-subtle">{r.section}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function DocsSidebar() {
  const pathname = usePathname();
  return (
    <nav aria-label="Documentation" className="space-y-6">
      <DocsSearch />
      {DOCS_NAV.map((s) => (
        <div key={s.section}>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-subtle">{s.section}</h2>
          <ul className="space-y-0.5">
            {s.links.map((l) => (
              <li key={l.href}>
                <Link
                  href={l.href}
                  className={cn(
                    "block rounded-md px-2.5 py-1.5 text-[13px] text-muted transition-colors hover:bg-surface-2 hover:text-foreground",
                    pathname === l.href && "bg-surface-2 font-medium text-foreground",
                  )}
                >
                  {l.title}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
