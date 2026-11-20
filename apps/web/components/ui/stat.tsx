import type { LucideIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

import { Card } from "./card";
import { Tooltip } from "./tooltip";

export function Stat({
  label,
  value,
  sub,
  icon: Icon,
  hint,
  className,
  children,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  icon?: LucideIcon;
  hint?: string;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <Card className={cn("p-5", className)}>
      <div className="flex items-center justify-between gap-2 text-[13px] text-muted">
        <span className="flex items-center gap-1.5">
          {label}
          {hint && (
            <Tooltip content={hint}>
              <button type="button" className="cursor-help text-subtle" aria-label={`About ${label}`}>
                ⓘ
              </button>
            </Tooltip>
          )}
        </span>
        {Icon && <Icon className="size-4 text-subtle" aria-hidden />}
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-[-0.02em]">{value}</div>
      {sub && <div className="mt-1 text-xs text-subtle">{sub}</div>}
      {children}
    </Card>
  );
}
