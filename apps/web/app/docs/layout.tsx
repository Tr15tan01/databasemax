import { DocsSidebar } from "@/features/docs/docs-sidebar";
import { SiteFooter } from "@/features/marketing/site-footer";
import { SiteHeader } from "@/features/marketing/site-header";

export const metadata = { title: { default: "Documentation", template: "%s · DatabaseMax Docs" } };

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader />
      <div className="mx-auto grid w-full max-w-6xl flex-1 gap-10 px-5 py-10 md:grid-cols-[220px_1fr]">
        <aside className="md:sticky md:top-20 md:h-[calc(100dvh-6rem)] md:overflow-y-auto">
          <DocsSidebar />
        </aside>
        <main id="main" className="min-w-0 pb-16">
          {children}
        </main>
      </div>
      <SiteFooter />
    </div>
  );
}
