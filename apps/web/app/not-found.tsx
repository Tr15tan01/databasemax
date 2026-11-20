import Link from "next/link";

import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-6 px-5 text-center">
      <Logo />
      <div>
        <p className="font-mono text-sm text-subtle">404</p>
        <h1 className="mt-2 text-2xl font-semibold tracking-[-0.02em]">This page doesn&apos;t exist</h1>
        <p className="mt-2 text-sm text-muted">Check the address, or head back to somewhere familiar.</p>
      </div>
      <div className="flex gap-2">
        <Button variant="secondary" asChild><Link href="/">Home</Link></Button>
        <Button asChild><Link href="/dashboard">Dashboard</Link></Button>
      </div>
    </div>
  );
}
