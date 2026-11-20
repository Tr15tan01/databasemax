import type { NextConfig } from "next";

const API_ORIGIN = process.env.DMX_API_ORIGIN ?? "http://127.0.0.1:8000";

// Content Security Policy for the web app. Paddle.js is only loaded on the billing page.
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://cdn.paddle.com" + (process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : ""),
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self' https://*.paddle.com",
  "frame-src https://*.paddle.com",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self' https://accounts.google.com",
].join("; ");

const config: NextConfig = {
  reactStrictMode: true,
  agentRules: false,
  poweredByHeader: false,
  output: "standalone",
  transpilePackages: ["@databasemax/shared-types"],
  async rewrites() {
    // In production Caddy routes /api/* to the API directly; this keeps dev same-origin.
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default config;
