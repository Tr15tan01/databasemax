import Link from "next/link";

import { Logo } from "@/components/logo";

export function AuthShell({ title, description, children, footer }: { title: string; description?: React.ReactNode; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <div className="relative flex min-h-dvh flex-col">
      <div aria-hidden className="hairline-grid pointer-events-none absolute inset-0 -z-10 opacity-50" />
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-5 py-5">
        <Logo />
        <Link href="/docs" className="text-[13px] text-muted hover:text-foreground">Docs</Link>
      </header>
      <main id="main" className="flex flex-1 items-start justify-center px-5 pb-16 pt-10 sm:pt-16">
        <div className="w-full max-w-[400px]">
          <div className="rounded-xl border border-border bg-surface p-7 shadow-[var(--shadow-pop)]">
            <h1 className="text-xl font-semibold tracking-[-0.02em]">{title}</h1>
            {description && <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{description}</p>}
            <div className="mt-6">{children}</div>
          </div>
          {footer && <div className="mt-6 text-center text-[13px] text-muted">{footer}</div>}
        </div>
      </main>
    </div>
  );
}

export function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className="size-4">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z" />
      <path fill="#FBBC05" d="M5.84 14.09A6.6 6.6 0 0 1 5.5 12c0-.73.13-1.43.34-2.09V7.07H2.18A11 11 0 0 0 1 12c0 1.78.43 3.45 1.18 4.93l3.66-2.84z" />
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
    </svg>
  );
}
