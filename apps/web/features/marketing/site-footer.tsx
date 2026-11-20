import Link from "next/link";

import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";

const COLS = [
  { title: "Product", links: [["Features", "/features"], ["Pricing", "/pricing"], ["Security", "/security"], ["Status", "/status"]] },
  { title: "Developers", links: [["Documentation", "/docs"], ["Getting started", "/docs/getting-started"], ["Connecting", "/docs/connection"], ["API reference", "/docs/api"]] },
  { title: "Company", links: [["About", "/about"], ["Contact", "/contact"], ["Privacy", "/privacy"], ["Terms", "/terms"]] },
];

export function SiteFooter() {
  return (
    <footer className="border-t border-border bg-surface/40">
      <div className="mx-auto grid max-w-6xl gap-10 px-5 py-14 md:grid-cols-[1.4fr_repeat(3,1fr)]">
        <div className="space-y-4">
          <Logo />
          <p className="max-w-xs text-[13px] leading-relaxed text-muted">
            Managed PostgreSQL for developers. Create a database, copy the connection URL, ship.
          </p>
          <ThemeToggle />
        </div>
        {COLS.map((c) => (
          <div key={c.title}>
            <h3 className="text-[13px] font-semibold">{c.title}</h3>
            <ul className="mt-3 space-y-2">
              {c.links.map(([label, href]) => (
                <li key={href}>
                  <Link href={href!} className="text-[13px] text-muted transition-colors hover:text-foreground">
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-border">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-5 py-5 text-xs text-subtle sm:flex-row sm:justify-between">
          <span>© {new Date().getFullYear()} DatabaseMax. PostgreSQL is a trademark of the PostgreSQL Community Association of Canada.</span>
          <span>Hosted on Oracle Cloud Infrastructure</span>
        </div>
      </div>
    </footer>
  );
}
