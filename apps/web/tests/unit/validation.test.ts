import { describe, expect, it } from "vitest";

import { safeNext } from "@/lib/hooks";
import { databaseSchema, loginSchema, projectSchema, registerSchema, resetSchema, slugPgName } from "@/lib/validation";

describe("auth forms", () => {
  it("rejects invalid email and empty password", () => {
    const r = loginSchema.safeParse({ email: "nope", password: "" });
    expect(r.success).toBe(false);
  });
  it("enforces password policy on registration", () => {
    expect(registerSchema.safeParse({ email: "a@b.co", password: "short", accept: true }).success).toBe(false);
    expect(registerSchema.safeParse({ email: "a@b.co", password: "aaaaaaaaaaaa", accept: true }).success).toBe(false);
    expect(registerSchema.safeParse({ email: "a@b.co", password: "a-good-passphrase", accept: false }).success).toBe(false);
    expect(registerSchema.safeParse({ email: "a@b.co", password: "a-good-passphrase", accept: true }).success).toBe(true);
  });
  it("requires matching passwords on reset", () => {
    const r = resetSchema.safeParse({ password: "a-good-passphrase", confirm: "different-passphrase" });
    expect(r.success).toBe(false);
  });
});

describe("resource forms", () => {
  it("validates project names", () => {
    expect(projectSchema.safeParse({ name: "My App", environment: "production" }).success).toBe(true);
    expect(projectSchema.safeParse({ name: "<script>", environment: "production" }).success).toBe(false);
    expect(projectSchema.safeParse({ name: "ok", environment: "prod" }).success).toBe(false);
  });
  it("validates database fields", () => {
    const base = { project_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6", name: "orders", postgres_version: "16", compute_profile: "nano", storage_mb: 500, environment: "development" as const };
    expect(databaseSchema.safeParse({ ...base, database_name: "orders_db" }).success).toBe(true);
    expect(databaseSchema.safeParse({ ...base, database_name: "postgres" }).success).toBe(false);
    expect(databaseSchema.safeParse({ ...base, database_name: "Bad-Name" }).success).toBe(false);
    expect(databaseSchema.safeParse({ ...base, storage_mb: 100 }).success).toBe(false);
    expect(databaseSchema.safeParse({ ...base, project_id: "x" }).success).toBe(false);
  });
  it("derives PostgreSQL names like the backend", () => {
    expect(slugPgName("My Orders DB")).toBe("my_orders_db");
    expect(slugPgName("123 app")).toBe("db_123_app");
    expect(slugPgName("postgres")).toBe("postgres_db");
    expect(slugPgName("---")).toBe("db");
  });
});

describe("redirect safety", () => {
  it("only allows relative same-site paths", () => {
    expect(safeNext("/databases")).toBe("/databases");
    expect(safeNext("//evil.com")).toBe("/dashboard");
    expect(safeNext("https://evil.com")).toBe("/dashboard");
    expect(safeNext("/\\evil.com")).toBe("/dashboard");
    expect(safeNext(null)).toBe("/dashboard");
  });
});
