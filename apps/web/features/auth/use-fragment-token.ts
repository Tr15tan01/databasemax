"use client";
import * as React from "react";

/** One-time tokens arrive in the URL fragment so they never reach server logs or Referer headers.
 * The fragment is removed from the address bar as soon as it has been read (once per mount, so
 * React Strict Mode's double effect doesn't lose it). */
export function useFragmentToken(): string | null | undefined {
  const [token, setToken] = React.useState<string | null | undefined>(undefined);
  const read = React.useRef(false);
  React.useEffect(() => {
    if (read.current) return;
    read.current = true;
    const m = window.location.hash.match(/token=([A-Za-z0-9_-]+)/);
    setToken(m ? m[1]! : null);
    if (m) window.history.replaceState(null, "", window.location.pathname);
  }, []);
  return token;
}
