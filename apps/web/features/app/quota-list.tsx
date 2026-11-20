import { Badge } from "@/components/ui/badge";
import { Meter } from "@/components/ui/progress";
import type { QuotaItem } from "@/lib/types";
import { formatBytes, formatMB } from "@/lib/utils";

const QUOTA_LABEL: Record<string, string> = {
  projects: "Projects",
  databases: "Databases",
  allocated_storage: "Allocated storage",
  storage_used: "Storage used",
};

function quotaValue(q: QuotaItem) {
  if (q.unit === "MB") return `${formatMB(q.used)} of ${formatMB(q.limit)}`;
  if (q.unit === "bytes") return `${formatBytes(q.used)} of ${formatBytes(q.limit)}`;
  return `${q.used} of ${q.limit}`;
}

export function QuotaList({ items }: { items: QuotaItem[] }) {
  return (
    <ul className="space-y-4">
      {items.map((q) => (
        <li key={q.resource}>
          <div className="mb-1.5 flex items-center justify-between gap-2 text-[13px]">
            <span className="flex shrink-0 items-center gap-2">
              {QUOTA_LABEL[q.resource] ?? q.resource}
              <Badge variant="outline" className="px-1.5 py-0 text-[10px]">{q.kind}</Badge>
            </span>
            <span className="text-right tabular text-muted">
              {quotaValue(q)}
              {q.percent !== null && q.percent >= 85 && (
                <span className={q.kind === "measured" ? "ml-1.5 font-medium text-warning" : "ml-1.5 text-subtle"}>
                  {q.kind === "measured" ? `(${q.percent}%)` : q.percent >= 100 ? "· limit reached" : ""}
                </span>
              )}
            </span>
          </div>
          <Meter value={q.percent ?? 0} alarm={q.kind === "measured"} label={`${QUOTA_LABEL[q.resource] ?? q.resource} usage`} />
        </li>
      ))}
    </ul>
  );
}

