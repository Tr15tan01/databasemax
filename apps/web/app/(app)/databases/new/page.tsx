"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { CreateDatabaseForm } from "@/features/databases/create-database-form";

function Inner() {
  const router = useRouter();
  const project = useSearchParams().get("project") ?? undefined;
  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader eyebrow={<Link href="/databases" className="hover:text-foreground">Databases</Link>} title="New database"
        description="An isolated PostgreSQL instance with its own credentials. Provisioning takes a few seconds." />
      <Card className="p-6">
        <CreateDatabaseForm defaultProjectId={project} onCreated={(d) => router.push(`/databases/${d.id}`)}
          secondary={<Button type="button" variant="secondary" asChild><Link href="/databases">Cancel</Link></Button>} />
      </Card>
    </div>
  );
}

export default function NewDatabasePage() {
  return <Suspense><Inner /></Suspense>;
}
