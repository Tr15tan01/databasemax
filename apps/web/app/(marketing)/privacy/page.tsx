import type { Metadata } from "next";

import { PageHero, Prose } from "@/features/marketing/page-hero";

export const metadata: Metadata = { title: "Privacy" };

export default function PrivacyPage() {
  return (
    <>
      <PageHero eyebrow="Legal" title="Privacy policy" description="Last updated: September 2026. This summary is not legal advice; have counsel review before launch." />
      <Prose>
        <h2>What we collect</h2>
        <ul>
          <li><strong>Account data:</strong> email address, optional display name, hashed password or Google account identifier.</li>
          <li><strong>Security data:</strong> sign-in attempts, IP addresses and user agents of sessions, and an audit log of sensitive actions.</li>
          <li><strong>Usage data:</strong> database storage size, connection counts, resource consumption and API request counts, used to show usage and enforce plan limits.</li>
          <li><strong>Billing data:</strong> handled by Paddle, our merchant of record. We store subscription status and transaction identifiers, not card numbers.</li>
        </ul>
        <h2>Your database contents</h2>
        <p>
          The data you store in your databases belongs to you. We don&apos;t read, analyze or sell it. Our staff access customer
          instances only when required to operate the service or when you ask us to, and such access is logged.
        </p>
        <h2>Analytics</h2>
        <p>
          The marketing site doesn&apos;t use third-party trackers. Product metrics shown in the dashboard come from your own
          account&apos;s measurements. Passwords, connection strings and SQL are never included in analytics or logs.
        </p>
        <h2>Retention and deletion</h2>
        <p>
          Deleting a database removes its instance, storage and credentials. Deleting your account deletes all databases and
          anonymizes your account record; audit and billing records are kept as long as legally required.
        </p>
        <h2>Contact</h2>
        <p>Questions: <a href="mailto:privacy@databasemax.dev">privacy@databasemax.dev</a>.</p>
      </Prose>
    </>
  );
}
