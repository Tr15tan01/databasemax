"use client";
import * as React from "react";

import { cn } from "@/lib/utils";

import { CopyButton } from "./copy-button";

type Lang = "bash" | "sql" | "python" | "javascript" | "typescript" | "json" | "go" | "ruby" | "http" | "text" | "dotenv" | "yaml";

const cache = new Map<string, string>();

/**
 * Syntax-highlighted code (Shiki, loaded on demand) with a copy button. Renders plain text
 * first so content is available immediately and without JavaScript. Highlighting output is
 * generated locally from the code string (Shiki escapes it), never from user HTML.
 */
export function CodeBlock({
  code,
  lang = "bash",
  title,
  className,
  copyValue,
  wrap,
}: {
  code: string;
  lang?: Lang;
  title?: React.ReactNode;
  className?: string;
  copyValue?: string;
  wrap?: boolean;
}) {
  const key = `${lang}:${code}`;
  const [html, setHtml] = React.useState<string | null>(() => cache.get(key) ?? null);
  React.useEffect(() => {
    let cancelled = false;
    if (cache.has(key)) {
      setHtml(cache.get(key)!);
      return;
    }
    import("shiki")
      .then(({ codeToHtml }) =>
        codeToHtml(code, {
          lang: lang === "text" || lang === "http" ? "text" : lang,
          themes: { light: "github-light", dark: "github-dark" },
          defaultColor: "light",
        }),
      )
      .then((out) => {
        cache.set(key, out);
        if (!cancelled) setHtml(out);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [key, code, lang]);

  return (
    <div className={cn("group relative overflow-hidden rounded-lg border border-border bg-surface-2/60", className)}>
      {title && (
        <div className="flex items-center justify-between border-b border-border px-4 py-2 text-xs text-muted">
          <span className="font-medium">{title}</span>
          <span className="font-mono text-[11px] uppercase tracking-wide text-subtle">{lang}</span>
        </div>
      )}
      <div className="absolute right-2 top-2 z-10" style={title ? { top: "2.6rem" } : undefined}>
        <CopyButton value={copyValue ?? code} className="bg-surface/80 backdrop-blur" />
      </div>
      <div
        className={cn(
          "overflow-x-auto p-4 pr-12 text-[13px] leading-6 [&_pre]:!bg-transparent",
          wrap && "[&_pre]:whitespace-pre-wrap [&_pre]:break-all",
        )}
      >
        {html ? (
          <div dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <pre className={cn("font-mono", wrap && "whitespace-pre-wrap break-all")}>
            <code>{code}</code>
          </pre>
        )}
      </div>
    </div>
  );
}
