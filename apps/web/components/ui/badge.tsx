import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap [&_svg]:size-3",
  {
    variants: {
      variant: {
        neutral: "border-border bg-surface-2 text-muted",
        accent: "border-transparent bg-accent-soft text-accent",
        good: "border-transparent bg-good-soft text-good",
        warning: "border-transparent bg-warning-soft text-warning",
        critical: "border-transparent bg-critical-soft text-critical",
        outline: "border-border text-muted",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export function Badge({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
