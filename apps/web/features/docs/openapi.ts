import spec from "@databasemax/shared-types/openapi.json";

/* Minimal OpenAPI 3.1 helpers for rendering the reference from the real FastAPI schema. */
export type Schema = {
  $ref?: string;
  type?: string | string[];
  properties?: Record<string, Schema>;
  required?: string[];
  items?: Schema;
  anyOf?: Schema[];
  allOf?: Schema[];
  enum?: unknown[];
  const?: unknown;
  description?: string;
  title?: string;
  format?: string;
  default?: unknown;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
};

export interface Param {
  name: string;
  in: string;
  required?: boolean;
  schema?: Schema;
  description?: string;
}

export interface Operation {
  method: string;
  path: string;
  summary?: string;
  description?: string;
  tags?: string[];
  parameters?: Param[];
  requestBody?: { content?: Record<string, { schema?: Schema }> };
  responses?: Record<string, { description?: string; content?: Record<string, { schema?: Schema }> }>;
  operationId?: string;
}

const s = spec as unknown as {
  paths: Record<string, Record<string, Omit<Operation, "method" | "path">>>;
  components: { schemas: Record<string, Schema> };
  info: { version: string };
};

export const API_VERSION = s.info.version;

export function resolve(schema?: Schema): Schema | undefined {
  if (!schema) return undefined;
  if (schema.$ref) return s.components.schemas[schema.$ref.split("/").pop()!];
  return schema;
}

export function refName(schema?: Schema): string | undefined {
  if (!schema) return undefined;
  if (schema.$ref) return schema.$ref.split("/").pop();
  if (schema.items?.$ref) return `${schema.items.$ref.split("/").pop()}[]`;
  return undefined;
}

export function typeLabel(schema?: Schema): string {
  if (!schema) return "any";
  if (schema.$ref) return schema.$ref.split("/").pop()!;
  if (schema.enum) return schema.enum.map((e) => JSON.stringify(e)).join(" | ");
  if (schema.const !== undefined) return JSON.stringify(schema.const);
  if (schema.anyOf) return schema.anyOf.map(typeLabel).filter((t) => t !== "null").join(" | ") + (schema.anyOf.some((x) => x.type === "null") ? " | null" : "");
  if (schema.type === "array") return `${typeLabel(schema.items)}[]`;
  if (Array.isArray(schema.type)) return schema.type.join(" | ");
  return schema.format ? `${schema.type} (${schema.format})` : (schema.type ?? "object");
}

const TAG_ORDER = ["auth", "account", "projects", "databases", "usage", "billing", "api-keys", "activity", "notifications", "meta"];
export const TAG_TITLES: Record<string, string> = {
  auth: "Authentication",
  account: "Account",
  projects: "Projects",
  databases: "Databases",
  usage: "Usage",
  billing: "Billing",
  "api-keys": "API keys",
  activity: "Activity",
  notifications: "Notifications",
  meta: "Service",
};

export function operationsByTag(): { tag: string; ops: Operation[] }[] {
  const groups = new Map<string, Operation[]>();
  for (const [path, methods] of Object.entries(s.paths)) {
    for (const [method, op] of Object.entries(methods)) {
      const tag = op.tags?.[0] ?? "other";
      if (tag === "admin" || tag === "dev") continue;
      if (!groups.has(tag)) groups.set(tag, []);
      groups.get(tag)!.push({ ...op, method: method.toUpperCase(), path });
    }
  }
  return TAG_ORDER.filter((t) => groups.has(t)).map((tag) => ({ tag, ops: groups.get(tag)! }));
}

export function opId(op: Operation) {
  return `${op.method.toLowerCase()}-${op.path.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "")}`;
}
