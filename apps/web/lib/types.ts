import type { Schemas } from "./api";

export type Me = Schemas["MeOut"];
export type Project = Schemas["ProjectOut"];
export type Database = Schemas["DatabaseOut"];
export type Connection = Schemas["ConnectionOut"];
export type DbEvent = Schemas["DatabaseEventOut"];
export type ProvisioningStatus = Schemas["ProvisioningStatus"];
export type Options = Schemas["OptionsOut"];
export type ApiKey = Schemas["ApiKeyOut"];
export type ApiKeyCreated = Schemas["ApiKeyCreated"];
export type AuditEvent = Schemas["AuditEventOut"];
export type Notification = Schemas["NotificationOut"];
export type SessionInfo = Schemas["SessionOut"];
export type Subscription = Schemas["SubscriptionOut"];
export type Payment = Schemas["PaymentOut"];
export type Backup = Schemas["BackupOut"];
export type BackupSummary = Schemas["BackupSummaryOut"];
export type Approval = Schemas["ApprovalOut"];
export type KeyActivity = Schemas["KeyActivityOut"];
export type SqlRun = Schemas["SqlRun"];
export type SqlRunResult = Schemas["SqlRunOut"];
export type SqlResultSet = Schemas["SqlResultSetOut"];
export type DbSchema = Schemas["SchemaOut"];
export type SchemaTable = Schemas["SchemaTableOut"];
export type Insights = Schemas["InsightsOut"];
export type Insight = Schemas["InsightOut"];
export type LiveSample = Schemas["LiveOut"];

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Plan {
  id: string;
  name: string;
  description?: string;
  price_cents: number;
  currency: string;
  features: string[];
  limits: Record<string, unknown>;
  purchasable?: boolean;
}

export interface QuotaItem {
  resource: string;
  used: number;
  limit: number;
  unit: string;
  kind: "count" | "allocated" | "measured";
  percent: number | null;
}

export interface Quotas {
  plan: { id: string; name: string; status: string };
  items: QuotaItem[];
  limits: Record<string, unknown>;
}

export interface SeriesPoint {
  t: string;
  v: number;
}

export interface DashboardSummary {
  counts: { projects: number; databases: number; running: number; provisioning: number; failed: number; stopped: number; provisioning_jobs_30d: number };
  storage: { allocated_mb: number; used_bytes: number; measured_at: string | null };
  connections: { active: number; measured_at: string | null };
  api_requests: { current_period: number; previous_period: number };
  quotas: Quotas;
  estimate: { plan: string; currency: string; amount_cents: number; next_billing_date: string | null; kind: string; explanation: string };
  recent_activity: { id: string; action: string; actor_type: string; resource_type: string | null; resource_id: string | null; outcome: string; created_at: string }[];
  recent_database_events: { id: string; database_id: string; database_name: string; event_type: string; to_status: string | null; message: string | null; created_at: string }[];
  backups: { count: number; stored_bytes: number; last_completed_at: string | null; failed_24h: number; retention_days: number };
}
