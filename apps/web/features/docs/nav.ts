export interface DocLink {
  href: string;
  title: string;
  keywords?: string;
}

export const DOCS_NAV: { section: string; links: DocLink[] }[] = [
  {
    section: "Overview",
    links: [
      { href: "/docs", title: "Introduction", keywords: "what is databasemax overview concepts projects databases" },
      { href: "/docs/getting-started", title: "Getting started", keywords: "register verify project create database first quickstart" },
    ],
  },
  {
    section: "Connecting",
    links: [
      { href: "/docs/connection", title: "Connection URLs", keywords: "url sslmode tls verify-full host port username password encoding" },
      { href: "/docs/connection#drivers", title: "Drivers & frameworks", keywords: "node pg prisma drizzle python psycopg sqlalchemy django go pgx ruby rails java jdbc" },
      { href: "/docs/connection#limits", title: "Connection limits", keywords: "pooling too many connections 53300 pgbouncer" },
      { href: "/docs/connection#troubleshooting", title: "Troubleshooting", keywords: "error ssl required unknown endpoint password authentication failed stopped" },
    ],
  },
  {
    section: "Operations",
    links: [
      { href: "/docs/backups", title: "Backups & restore", keywords: "backup restore clone copy retention pg_dump encryption verify final point in time pitr" },
    ],
  },
  {
    section: "API",
    links: [
      { href: "/docs/api", title: "API reference", keywords: "rest http endpoints openapi" },
      { href: "/docs/api#authentication", title: "Authentication & API keys", keywords: "bearer token scopes api key" },
      { href: "/docs/api#errors", title: "Errors & request IDs", keywords: "error code request id rate limit 429" },
      { href: "/docs/api#idempotency", title: "Idempotency", keywords: "idempotency-key retries duplicate" },
      { href: "/docs/agents", title: "Agents & approvals", keywords: "ai agent machine identity automation approval pending_approval 202 destructive" },
    ],
  },
];
