export interface BackupOverview {
  by_status: Record<string, number>;
  stored_bytes: number;
  last_24h: Record<string, number>;
  ready_databases: number;
  ready_databases_with_recent_backup: number;
  verification_30d: { passed: number; failed: number };
  avg_duration_ms_7d: number | null;
  provisioner?: { store: string; active_key_id: string; used_bytes: number | null; budget_mb?: number } | null;
}
