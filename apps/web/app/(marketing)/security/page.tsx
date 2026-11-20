import type { Metadata } from "next";

import { PageHero, Prose } from "@/features/marketing/page-hero";

export const metadata: Metadata = { title: "Security" };

const STATUS: [string, string, string][] = [
  ["TLS required on all database connections", "Implemented", "good"],
  ["One PostgreSQL instance per database, capabilities dropped, read-only root filesystem", "Implemented", "good"],
  ["Per-database credentials, sent to PostgreSQL as SCRAM verifiers", "Implemented", "good"],
  ["Passwords encrypted at rest (AES-256-GCM)", "Implemented", "good"],
  ["Tenant isolation checks + PostgreSQL row-level security in the control plane", "Implemented", "good"],
  ["Argon2id password hashing, login throttling, session revocation", "Implemented", "good"],
  ["Audit log of sensitive actions", "Implemented", "good"],
  ["Daily encrypted backups (AES-256-GCM, keys held only by the backup service) with periodic restore tests", "Implemented", "good"],
  ["Off-host backup copies (object storage)", "Supported; depends on deployment configuration", "warn"],
  ["Hard per-database storage limits (dedicated fixed-size filesystems for data and WAL)", "Implemented", "good"],
  ["Encryption of internal gateway-to-database traffic", "Planned (Phase 4)", "warn"],
  ["Independent penetration test", "Planned (Phase 4)", "warn"],
  ["Two-factor authentication", "Planned", "warn"],
];

export default function SecurityPage() {
  return (
    <>
      <PageHero
        eyebrow="Security"
        title="How we protect your data — and what's still on the list."
        description="Security is designed in from the start. This page describes what is implemented and tested today, and what isn't yet."
      />
      <Prose>
        <h2>Isolation</h2>
        <p>
          Every database runs as its own PostgreSQL server under its own unprivileged system user, with its own storage
          filesystem. Each server runs in a sandbox: no Linux capabilities, <code>no-new-privileges</code>, a read-only system,
          a private view of the filesystem that contains only its own data, other users&apos; processes hidden, and CPU, memory
          and process limits. It listens only on a private address and cannot open outbound network connections.
        </p>
        <p>
          Your database role owns its database but is not a superuser and cannot create roles, databases or replication
          connections. Administrative access to each instance is only possible from the same machine, with a per-instance
          password that never leaves the provisioning service.
        </p>
        <h2>Connections</h2>
        <p>
          All client connections go through the DatabaseMax gateway, which requires TLS 1.2 or newer and routes each connection to
          the right database using the hostname (SNI) and your unique username. Authentication happens end to end with your
          database using SCRAM-SHA-256 — the gateway never sees your password. Plain-text connections are rejected.
        </p>
        <h2>Credentials</h2>
        <ul>
          <li>Database passwords are 32 random characters, encrypted at rest with AES-256-GCM.</li>
          <li>PostgreSQL only ever receives a SCRAM verifier, not the password.</li>
          <li>Revealing a password is an explicit, logged action that requires a recent sign-in.</li>
          <li>Rotation revokes the old password immediately and closes open sessions.</li>
          <li>Passwords and tokens are filtered from application logs; this is covered by automated tests.</li>
          <li>DatabaseMax staff can&apos;t view your database password in the admin tools.</li>
        </ul>
        <h2>Accounts</h2>
        <p>
          Account passwords are hashed with Argon2id. Sign-in attempts are throttled progressively, sessions live in HttpOnly
          cookies (never in browser storage) and can be revoked from Settings. State-changing requests are protected against
          CSRF. API keys are hashed, scoped, optionally restricted to projects, and revocable.
        </p>
        <h2>Status of controls</h2>
        <table>
          <thead>
            <tr><th>Control</th><th>Status</th></tr>
          </thead>
          <tbody>
            {STATUS.map(([c, s, tone]) => (
              <tr key={c}>
                <td>{c}</td>
                <td className={tone === "good" ? "!text-good" : "!text-warning"}>{s}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>
          These controls have automated tests but have not yet been independently reviewed. We&apos;ll publish the results of an
          external review when one has been completed.
        </p>
        <h2>Reporting a vulnerability</h2>
        <p>
          Email <a href="mailto:security@databasemax.dev">security@databasemax.dev</a>. Please don&apos;t access other customers&apos;
          data or degrade the service while testing. We&apos;ll acknowledge reports within two business days.
        </p>
      </Prose>
    </>
  );
}
