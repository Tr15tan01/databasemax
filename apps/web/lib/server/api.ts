import "server-only";

import type { Plan } from "@/lib/types";

const API_ORIGIN = process.env.DMX_API_ORIGIN ?? "http://127.0.0.1:8000";

/** Server-side fetch of public API data (plans, status). Returns null when the API is unreachable. */
export async function serverGet<T>(path: string, revalidate = 60): Promise<T | null> {
  try {
    const res = await fetch(`${API_ORIGIN}/api/v1${path}`, { next: { revalidate }, signal: AbortSignal.timeout(4000) });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export const getPlans = () => serverGet<Plan[]>("/plans", 300);
