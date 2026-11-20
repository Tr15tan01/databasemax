"use client";
import { Check, Copy } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

export function CopyButton({ value, label = "Copy", className, onCopied }: { value: string; label?: string; className?: string; onCopied?: () => void }) {
  const [copied, setCopied] = React.useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = value;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    onCopied?.();
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
      className={cn(
        "inline-grid size-7 shrink-0 place-items-center rounded-md text-subtle transition-colors hover:bg-surface-3 hover:text-foreground",
        className,
      )}
    >
      {copied ? <Check className="size-3.5 text-good" /> : <Copy className="size-3.5" />}
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </button>
  );
}
