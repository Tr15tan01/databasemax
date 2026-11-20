import type { Metadata } from "next";

import { PageHero, Prose } from "@/features/marketing/page-hero";

export const metadata: Metadata = { title: "About" };

export default function AboutPage() {
  return (
    <>
      <PageHero eyebrow="About" title="Databases should be the easy part." />
      <Prose>
        <p>
          DatabaseMax is a small, developer-focused managed PostgreSQL service. We think getting a real, isolated PostgreSQL
          database should take seconds — without learning a proprietary platform, installing a CLI or connecting a Git provider.
        </p>
        <h2>What we believe</h2>
        <ul>
          <li><strong>Real PostgreSQL.</strong> Standard connection URLs and standard drivers. Your data isn&apos;t locked in.</li>
          <li><strong>Honest claims.</strong> We only advertise what&apos;s built and tested. Our roadmap is public and labeled as such.</li>
          <li><strong>Transparent limits.</strong> Every plan limit is visible and enforced; usage numbers are measured, not guessed.</li>
          <li><strong>Security by default.</strong> TLS, isolation and least privilege aren&apos;t upsells.</li>
        </ul>
        <h2>Where we run</h2>
        <p>
          DatabaseMax runs on Oracle Cloud Infrastructure. The initial platform is a single, carefully partitioned host with
          resources reserved for the control plane; we&apos;ll expand to multiple hosts and regions as we grow.
        </p>
      </Prose>
    </>
  );
}
