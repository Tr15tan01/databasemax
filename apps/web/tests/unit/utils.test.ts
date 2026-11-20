import { describe, expect, it } from "vitest";

import { fillDays, formatBytes, formatCents, formatMB, pct } from "@/lib/utils";

describe("formatting", () => {
  it("formats bytes", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(7_654_321)).toBe("7.3 MB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
  });
  it("formats megabytes and cents", () => {
    expect(formatMB(500)).toBe("500 MB");
    expect(formatMB(5120)).toBe("5 GB");
    expect(formatCents(900)).toBe("$9");
    expect(formatCents(2950)).toBe("$29.50");
  });
  it("computes capped percentages", () => {
    expect(pct(5, 10)).toBe(50);
    expect(pct(20, 10)).toBe(100);
    expect(pct(1, 0)).toBe(0);
  });
  it("fills missing days with zeros for count series", () => {
    const today = new Date().toISOString().slice(0, 10);
    const out = fillDays([{ t: `${today}T00:00:00Z`, v: 7 }], 5);
    expect(out).toHaveLength(5);
    expect(out.at(-1)!.v).toBe(7);
    expect(out.slice(0, 4).every((p) => p.v === 0)).toBe(true);
  });
});
