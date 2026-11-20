const ACTIONS: Record<string, string> = {
  "auth.register": "Created account",
  "auth.email_verified": "Verified email",
  "auth.login": "Signed in",
  "auth.login_failed": "Failed sign-in attempt",
  "auth.logout": "Signed out",
  "auth.password_changed": "Changed password",
  "auth.password_reset": "Reset password",
  "auth.password_reset_requested": "Requested password reset",
  "auth.session_revoked": "Revoked a session",
  "auth.sessions_revoked": "Signed out other sessions",
  "auth.oauth_linked": "Linked Google account",
  "auth.reauth_failed": "Failed password confirmation",
  "auth.register_duplicate": "Sign-up attempted with your email",
  "account.update": "Updated profile",
  "account.delete": "Deleted account",
  "project.create": "Created project",
  "project.update": "Updated project",
  "project.archive": "Archived project",
  "project.unarchive": "Restored project",
  "project.delete": "Deleted project",
  "database.create": "Requested database",
  "database.ready": "Database became ready",
  "database.update": "Updated database settings",
  "database.delete": "Requested database deletion",
  "database.deleted": "Database deleted",
  "database.stop": "Stopped database",
  "database.start": "Started database",
  "database.retry": "Retried provisioning",
  "database.credentials_rotate": "Requested credential rotation",
  "database.credentials_rotated": "Rotated credentials",
  "database.credentials_revealed": "Revealed database password",
  "api_key.create": "Created API key",
  "api_key.revoke": "Revoked API key",
  "api_key.update": "Changed API key settings",
  "approval.requested": "Asked for approval",
  "approval.approved": "Approved a request",
  "approval.rejected": "Rejected a request",
  "backup.delete": "Deleted a backup",
  "billing.checkout_started": "Started checkout",
  "billing.subscription_changed": "Subscription changed",
  "billing.cancel_requested": "Requested cancellation",
  "billing.plan_change_requested": "Requested plan change",
  "billing.database_stopped": "Database stopped by billing policy",
  "job.create.failed": "Provisioning failed",
  "admin.user_suspend": "Account suspended by support",
  "admin.user_reactivate": "Account reactivated by support",
  "admin.user_email_verified": "Email marked verified by support",
  "admin.database_suspend": "Database suspended by support",
  "admin.database_unsuspend": "Database suspension lifted",
  "backup.create": "Started a backup",
  "backup.verification_failed": "Backup restore check failed",
  "database.clone": "Created a database from a backup or copy",
  "admin.backup_create": "Backup taken by support",
  "admin.backup_verify": "Backup verification by support",
  "job.backup.failed": "Backup failed",
  "job.clone.failed": "Restore into new database failed",
};

export const APPROVAL_STATUS: Record<string, { label: string; variant: "neutral" | "accent" | "good" | "warning" | "critical" | "outline" }> = {
  pending: { label: "Waiting for you", variant: "warning" },
  executed: { label: "Approved · done", variant: "good" },
  approved: { label: "Approved", variant: "accent" },
  failed: { label: "Approved · failed", variant: "critical" },
  rejected: { label: "Rejected", variant: "neutral" },
  expired: { label: "Expired", variant: "outline" },
};

export function actionLabel(action: string): string {
  return ACTIONS[action] ?? action.replace(/[._]/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export const ACTOR_LABELS: Record<string, string> = { user: "You", api_key: "API key", admin: "Support", system: "System" };
/** Operator-console wording (the customer-facing labels say "You"). */
export const ADMIN_ACTOR_LABELS: Record<string, string> = { user: "User", api_key: "API key", admin: "Admin", system: "System" };

const EVENTS: Record<string, string> = {
  requested: "Creation requested",
  status_changed: "Status changed",
  credentials_rotated: "Credentials rotated",
  credentials_rotation_requested: "Rotation requested",
  settings_changed: "Settings changed",
  attempt_failed: "Attempt failed",
  storage_limit_reached: "Storage limit reached",
  storage_limit_cleared: "Storage back under limit",
  connection_limit_changed: "Connection limit changed",
  reconciled: "Recovered after interruption",
  health_check_failed: "Health check failed",
  instance_missing: "Instance missing",
  delete_failed: "Deletion failed",
  backup_requested: "Backup requested",
  backup_completed: "Backup completed",
  backup_failed: "Backup failed",
  cloned: "Copied into a new database",
  source_backed_up: "Source backed up for copy",
  data_restored: "Data restored",
};

export function eventLabel(e: { event_type: string; to_status?: string | null }): string {
  if (e.event_type === "status_changed" && e.to_status) return `→ ${e.to_status}`;
  return EVENTS[e.event_type] ?? e.event_type.replace(/_/g, " ");
}

export const ENV_LABELS: Record<string, string> = { development: "Development", staging: "Staging", production: "Production" };
