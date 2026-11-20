import { cn } from "@/lib/utils";

/** Quota meter. Color escalates at 85%/100% and the state is also stated in text by callers. */
export function Meter({ value, className, label, alarm = true }: { value: number; className?: string; label: string; alarm?: boolean }) {
  const v = Math.max(0, Math.min(100, value));
  const tone = !alarm ? "bg-accent" : v >= 100 ? "bg-critical" : v >= 85 ? "bg-warning" : "bg-accent";
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuenow={Math.round(v)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-3", className)}
    >
      <div className={cn("h-full rounded-full transition-[width] duration-500", tone)} style={{ width: `${Math.max(v, v > 0 ? 2 : 0)}%` }} />
    </div>
  );
}
