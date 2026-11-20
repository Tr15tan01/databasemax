import * as React from "react";

import { cn } from "@/lib/utils";

import { Label } from "./label";

export function FormField({
  id,
  label,
  hint,
  error,
  children,
  className,
  optional,
}: {
  id: string;
  label: React.ReactNode;
  hint?: React.ReactNode;
  error?: string;
  children: React.ReactNode;
  className?: string;
  optional?: boolean;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex items-baseline justify-between">
        <Label htmlFor={id}>{label}</Label>
        {optional && <span className="text-xs text-subtle">Optional</span>}
      </div>
      {children}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-[13px] text-critical">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-[13px] text-subtle">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
