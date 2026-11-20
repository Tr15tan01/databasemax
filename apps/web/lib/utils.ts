import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : digits)} ${units[i]}`;
}

export function formatMB(mb: number | null | undefined): string {
  if (mb === null || mb === undefined) return "—";
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB` : `${mb} MB`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(n);
}

export function formatCents(cents: number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency, minimumFractionDigits: cents % 100 ? 2 : 0 }).format(
    cents / 100,
  );
}

export function formatDate(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium" }) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-US", opts).format(new Date(iso));
}

export function formatDateTime(iso: string | null | undefined) {
  return formatDate(iso, { dateStyle: "medium", timeStyle: "short" });
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const table: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [3600, "minute"],
    [86400, "hour"],
    [604800, "day"],
    [2629800, "week"],
    [31557600, "month"],
    [Infinity, "year"],
  ];
  const divisors: Record<string, number> = { second: 1, minute: 60, hour: 3600, day: 86400, week: 604800, month: 2629800, year: 31557600 };
  for (const [limit, unit] of table) {
    if (abs < limit) return rtf.format(-Math.round(diff / divisors[unit]!), unit);
  }
  return formatDate(iso);
}

export function pct(used: number, limit: number | null | undefined): number {
  if (!limit) return 0;
  return Math.min(100, Math.round((used / limit) * 1000) / 10);
}

export function humanize(s: string): string {
  return s.replace(/[._]/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** For count metrics (events per day), days without events are real zeros: fill them in. */
export function fillDays(points: { t: string; v: number }[], days: number): { t: string; v: number }[] {
  const byDay = new Map(points.map((p) => [p.t.slice(0, 10), p.v]));
  const out: { t: string; v: number }[] = [];
  const now = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - i));
    const key = d.toISOString().slice(0, 10);
    out.push({ t: `${key}T00:00:00Z`, v: byDay.get(key) ?? 0 });
  }
  return out;
}
