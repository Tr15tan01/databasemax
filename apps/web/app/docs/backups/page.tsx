import { CodeBlock } from "@/components/ui/code-block";
import { Callout, DocHeader } from "@/features/docs/doc-page";

export const metadata = { title: "Backups & restore" };

export default function BackupsDocs() {
  return (
    <div className="prose-docs">
      <DocHeader
        title="Backups & restore"
        lead="Running databases are backed up daily. Backups are encrypted before they are stored, and restoring always creates a new database."
      />

      <h2 id="what">What is backed up</h2>
      <p>
        A backup is a logical snapshot made with <code>pg_dump</code> (custom format) inside your database&apos;s own instance, so
        the tools always match your PostgreSQL version. It contains your schemas, tables and data, indexes, constraints, views,
        functions, sequences and extensions you created. Role memberships and <code>GRANT</code>s are not included: a restored
        database belongs to its own new role.
      </p>
      <table>
        <thead><tr><th>Type</th><th>When</th></tr></thead>
        <tbody>
          <tr><td>Daily</td><td>Automatically, about once every 24 hours while the database is running.</td></tr>
          <tr><td>On demand</td><td>When you click <em>Back up now</em> or call the API. Free: 1 per database per day, Developer: 5, Pro: 20.</td></tr>
          <tr><td>Final</td><td>Optionally, right before a database is deleted.</td></tr>
          <tr><td>Before copy</td><td>Taken automatically when you copy a database&apos;s current data.</td></tr>
        </tbody>
      </table>
      <p>Backups are kept for your plan&apos;s retention period (Free 3 days, Developer 7, Pro 14) and then deleted automatically.</p>
      <Callout tone="warning" title="No point-in-time recovery">
        You can restore to the moment a backup was taken, not to an arbitrary point in time. Changes made after the latest backup
        are not in any backup. Stopped databases aren&apos;t backed up while stopped (nothing changes while they&apos;re stopped).
      </Callout>

      <h2 id="security">How backups are protected</h2>
      <ul>
        <li>The dump is encrypted with AES-256-GCM as it streams out of your instance; unencrypted backup data never touches disk and never passes through our web or API servers.</li>
        <li>Encryption keys are held only by the internal service that takes and restores backups.</li>
        <li>Every 1 MiB chunk is authenticated and numbered, so a modified, reordered or truncated backup is rejected. Before a restore starts, the whole backup is checked against the checksum recorded when it was taken.</li>
        <li>Backups are regularly restored into isolated test instances that have no network path to customers, checked, and destroyed.</li>
      </ul>

      <h2 id="restore">Restoring</h2>
      <p>
        Open a database&apos;s <strong>Backups</strong> tab (or the <strong>Backups</strong> page for deleted databases) and choose
        <em> Restore</em>. You get a <strong>new</strong> database with its own host, username and password; the source database is
        never modified. When it&apos;s ready, point your application at the new connection URL. The new database counts toward your
        plan&apos;s database and storage limits.
      </p>
      <p>
        <strong>Copy current data</strong> works the same way but first takes a fresh backup of the running source — useful for a
        staging copy of production or rehearsing a migration.
      </p>

      <h2 id="api">API</h2>
      <p>API keys need the <code>backups:read</code> and/or <code>backups:write</code> scopes (restores also need <code>databases:write</code>).</p>
      <CodeBlock lang="bash" code={`# list backups of a database
curl -H "Authorization: Bearer $DMX_API_KEY" https://databasemax.dev/api/v1/databases/$DB_ID/backups

# take an on-demand backup (asynchronous; poll GET /backups/{id})
curl -X POST -H "Authorization: Bearer $DMX_API_KEY" https://databasemax.dev/api/v1/databases/$DB_ID/backups

# restore a backup into a new database
curl -X POST -H "Authorization: Bearer $DMX_API_KEY" -H "Content-Type: application/json" \\
  -d '{"name": "app-restored", "backup_id": "'$BACKUP_ID'"}' \\
  https://databasemax.dev/api/v1/databases/$DB_ID/clone

# delete a database, keeping a final backup
curl -X DELETE -H "Authorization: Bearer $DMX_API_KEY" \\
  "https://databasemax.dev/api/v1/databases/$DB_ID?confirm=my-db&final_backup=true"`} />

      <h2 id="own-copies">Keeping your own copies</h2>
      <p>You can always export your data yourself with the connection URL:</p>
      <CodeBlock lang="bash" code={`pg_dump "$DATABASE_URL" --format=custom --no-owner --file=app.dump
pg_restore --no-owner --dbname "$OTHER_DATABASE_URL" app.dump`} />
    </div>
  );
}
