"use client";
import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import * as React from "react";

import { cn } from "@/lib/utils";

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  const options = [
    { id: "light", icon: Sun, label: "Light" },
    { id: "system", icon: Monitor, label: "System" },
    { id: "dark", icon: Moon, label: "Dark" },
  ] as const;
  return (
    <div role="radiogroup" aria-label="Theme" className={cn("inline-flex rounded-full border border-border bg-surface p-0.5", className)}>
      {options.map(({ id, icon: Icon, label }) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={mounted && theme === id}
          aria-label={label}
          onClick={() => setTheme(id)}
          className={cn(
            "grid size-6 place-items-center rounded-full text-subtle transition-colors hover:text-foreground",
            mounted && theme === id && "bg-surface-3 text-foreground",
          )}
        >
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
