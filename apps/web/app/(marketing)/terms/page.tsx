import type { Metadata } from "next";

import { PageHero, Prose } from "@/features/marketing/page-hero";

export const metadata: Metadata = { title: "Terms" };

export default function TermsPage() {
  return (
    <>
      <PageHero eyebrow="Legal" title="Terms of service" description="Last updated: September 2026. This summary is not legal advice; have counsel review before launch." />
      <Prose>
        <h2>The service</h2>
        <p>DatabaseMax provides managed PostgreSQL databases. Features and limits are described on the pricing page and enforced by the service.</p>
        <h2>Availability and data protection</h2>
        <p>
          The service currently runs on a single host without automatic failover. Running databases are backed up daily and
          backups are kept for your plan&apos;s retention period; restores return data as of the time a backup was taken
          (there is no point-in-time recovery). We recommend also keeping your own copies of data you can&apos;t afford to lose
          (for example with <code>pg_dump</code>). We&apos;ll update these terms when higher availability is offered.
        </p>
        <h2>Acceptable use</h2>
        <ul>
          <li>No attempts to access other customers&apos; data or to escape isolation.</li>
          <li>No use of databases for malware distribution, spam or illegal content.</li>
          <li>No load that intentionally degrades the service for others.</li>
        </ul>
        <h2>Billing</h2>
        <p>
          Paid plans are billed monthly in advance by Paddle. A failed payment starts a 14-day grace period; databases are never
          deleted because of a failed payment, but resources above Free plan limits may be stopped after the grace period.
        </p>
        <h2>Termination</h2>
        <p>You may delete your account at any time from Settings. We may suspend accounts that violate these terms, with notice where possible.</p>
      </Prose>
    </>
  );
}
