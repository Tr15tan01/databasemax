import { AppShell } from "@/features/app/app-shell";

export const metadata = { robots: { index: false } };

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
