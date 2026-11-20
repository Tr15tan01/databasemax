import { CircleAlert, CircleCheck, CircleDashed, CirclePause, CircleX, Loader2, Trash2 } from "lucide-react";

import { Badge } from "./badge";

type Entry = { label: string; variant: "good" | "warning" | "critical" | "neutral" | "accent"; icon: React.ElementType; spin?: boolean };

const MAP: Record<string, Entry> = {
  ready: { label: "Running", variant: "good", icon: CircleCheck },
  pending: { label: "Pending", variant: "accent", icon: Loader2, spin: true },
  validating: { label: "Validating", variant: "accent", icon: Loader2, spin: true },
  queued: { label: "Queued", variant: "accent", icon: CircleDashed },
  provisioning: { label: "Provisioning", variant: "accent", icon: Loader2, spin: true },
  configuring: { label: "Configuring", variant: "accent", icon: Loader2, spin: true },
  testing: { label: "Testing", variant: "accent", icon: Loader2, spin: true },
  failed: { label: "Failed", variant: "critical", icon: CircleX },
  stopping: { label: "Stopping", variant: "warning", icon: Loader2, spin: true },
  stopped: { label: "Stopped", variant: "neutral", icon: CirclePause },
  deleting: { label: "Deleting", variant: "warning", icon: Trash2 },
  deleted: { label: "Deleted", variant: "neutral", icon: Trash2 },
  migrating: { label: "Moving", variant: "warning", icon: Loader2, spin: true },
  draining: { label: "Draining", variant: "warning", icon: CirclePause },
  offline: { label: "Offline", variant: "critical", icon: CircleX },
  retired: { label: "Retired", variant: "neutral", icon: Trash2 },
  active: { label: "Active", variant: "good", icon: CircleCheck },
  archived: { label: "Archived", variant: "neutral", icon: CirclePause },
  suspended: { label: "Suspended", variant: "critical", icon: CircleAlert },
};

export function StatusBadge({ status }: { status: string }) {
  const m: Entry = MAP[status] ?? { label: status, variant: "neutral", icon: CircleDashed };
  const Icon = m.icon;
  return (
    <Badge variant={m.variant}>
      <Icon className={m.spin ? "animate-spin" : undefined} aria-hidden />
      {m.label}
    </Badge>
  );
}

export const IN_FLIGHT = new Set(["pending", "validating", "queued", "provisioning", "configuring", "testing", "stopping", "deleting", "migrating"]);
