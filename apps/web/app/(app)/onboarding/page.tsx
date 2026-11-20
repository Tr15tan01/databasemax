"use client";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Check, Database as DbIcon, FolderKanban, Plug, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConnectionDetails } from "@/features/databases/connection-details";
import { CreateDatabaseForm } from "@/features/databases/create-database-form";
import { ProvisioningProgress } from "@/features/databases/provisioning-progress";
import { useDatabase } from "@/features/databases/use-database";
import { CreateProjectForm } from "@/features/projects/create-project-form";
import { api } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import type { Page, Project } from "@/lib/types";
import { cn } from "@/lib/utils";

const STEPS = [
  { title: "Welcome", icon: Sparkles },
  { title: "Project", icon: FolderKanban },
  { title: "Database", icon: DbIcon },
  { title: "Provisioning", icon: DbIcon },
  { title: "Connect", icon: Plug },
];

function Stepper({ step }: { step: number }) {
  return (
    <ol className="mb-10 flex items-center gap-2" aria-label="Setup progress">
      {STEPS.map((s, i) => (
        <li key={s.title} className="flex flex-1 items-center gap-2" aria-current={i === step ? "step" : undefined}>
          <span className={cn("grid size-6 shrink-0 place-items-center rounded-full border text-[11px] font-medium",
            i < step ? "border-good bg-good text-white" : i === step ? "border-foreground text-foreground" : "border-border text-subtle")}>
            {i < step ? <Check className="size-3.5" /> : i + 1}
          </span>
          <span className={cn("hidden text-[13px] sm:inline", i === step ? "font-medium" : "text-muted")}>{s.title}</span>
          {i < STEPS.length - 1 && <span className="h-px flex-1 bg-border" aria-hidden />}
        </li>
      ))}
    </ol>
  );
}

function DatabaseSteps({ id, onReady }: { id: string; onReady: () => void }) {
  const { data } = useDatabase(id);
  const ready = data?.status === "ready";
  React.useEffect(() => { if (ready) onReady(); }, [ready, onReady]);
  if (!data) return null;
  return <ProvisioningProgress database={data} />;
}

export default function OnboardingPage() {
  const router = useRouter();
  const { data: me } = useMe();
  const projects = useQuery({ queryKey: ["projects", "all"], queryFn: () => api<Page<Project>>("/projects", { query: { page_size: 1 } }) });
  const [step, setStep] = React.useState(0);
  const [project, setProject] = React.useState<Project | null>(null);
  const [dbId, setDbId] = React.useState<string | null>(null);
  const db = useDatabase(dbId);

  React.useEffect(() => {
    // Returning users who already have a project skip straight to the database step.
    if (step === 0 && projects.data?.items[0] && !project) setProject(projects.data.items[0]);
  }, [projects.data, step, project]);

  const onReady = React.useCallback(() => setTimeout(() => setStep(4), 900), []);

  return (
    <div className="min-h-dvh">
      <header className="mx-auto flex max-w-3xl items-center justify-between px-5 py-5">
        <Logo href="/dashboard" />
        <Link href="/dashboard" className="text-[13px] text-muted hover:text-foreground">Skip setup</Link>
      </header>
      <main id="main" className="mx-auto max-w-3xl px-5 pb-20 pt-6">
        <Stepper step={step} />
        {step === 0 && (
          <div className="animate-fade-in">
            <h1 className="text-3xl font-semibold tracking-[-0.025em]">Welcome to DatabaseMax{me?.display_name ? `, ${me.display_name.split(" ")[0]}` : ""}.</h1>
            <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
              In the next minute you&apos;ll create a project, provision an isolated PostgreSQL database, and get a connection URL for your app.
            </p>
            <ul className="mt-8 grid gap-3 sm:grid-cols-3">
              {[["Project", "A home for related databases."], ["Database", "Your own PostgreSQL instance with TLS."], ["Connection URL", "Paste it into your app's environment."]].map(([t, d]) => (
                <li key={t} className="rounded-lg border border-border bg-surface p-4">
                  <div className="text-sm font-medium">{t}</div>
                  <div className="mt-1 text-[13px] text-muted">{d}</div>
                </li>
              ))}
            </ul>
            {!me?.email_verified && (
              <p className="mt-6 rounded-md border border-warning/30 bg-warning-soft px-3 py-2 text-[13px]">You&apos;ll need to verify your email before the database step — check your inbox.</p>
            )}
            <Button size="lg" className="mt-8" onClick={() => setStep(project ? 2 : 1)}>Get started <ArrowRight /></Button>
          </div>
        )}
        {step === 1 && (
          <Card className="animate-fade-in p-6 sm:p-8">
            <h1 className="text-xl font-semibold tracking-[-0.02em]">Create your first project</h1>
            <p className="mb-6 mt-1 text-[13px] text-muted">Projects group databases for one app or service.</p>
            <CreateProjectForm submitLabel="Continue" onCreated={(p) => { setProject(p); setStep(2); }} />
          </Card>
        )}
        {step === 2 && (
          <Card className="animate-fade-in p-6 sm:p-8">
            <h1 className="text-xl font-semibold tracking-[-0.02em]">Create your first PostgreSQL database</h1>
            <p className="mb-6 mt-1 text-[13px] text-muted">In project <strong className="text-foreground">{project?.name}</strong>. You can change the name and storage later.</p>
            <CreateDatabaseForm defaultProjectId={project?.id} onCreated={(d) => { setDbId(d.id); setStep(3); }} />
          </Card>
        )}
        {step === 3 && dbId && (
          <Card className="animate-fade-in p-6 sm:p-8">
            <h1 className="text-xl font-semibold tracking-[-0.02em]">Provisioning your database</h1>
            <p className="mb-6 mt-1 text-[13px] text-muted">This usually takes a few seconds. You can leave this page — we&apos;ll notify you when it&apos;s ready.</p>
            <DatabaseSteps id={dbId} onReady={onReady} />
          </Card>
        )}
        {step === 4 && db.data && (
          <div className="animate-fade-in">
            <h1 className="text-2xl font-semibold tracking-[-0.02em]">🎉 {db.data.name} is ready</h1>
            <p className="mb-6 mt-1 text-[13px] text-muted">Copy the connection details into your application.</p>
            <ConnectionDetails database={db.data} />
            <div className="mt-6 flex justify-end gap-2">
              <Button variant="secondary" asChild><Link href="/docs/connection">Connection guide</Link></Button>
              <Button onClick={() => router.push(`/databases/${db.data!.id}`)}>Go to database <ArrowRight /></Button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
