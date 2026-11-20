import { z } from "zod";

export const emailSchema = z.string().trim().min(1, "Enter your email address.").email("Enter a valid email address.");
export const passwordSchema = z
  .string()
  .min(10, "Use at least 10 characters.")
  .max(128, "Use at most 128 characters.")
  .refine((p) => new Set(p).size >= 4, "Password is too repetitive.");

export const loginSchema = z.object({ email: emailSchema, password: z.string().min(1, "Enter your password.") });
export const registerSchema = z.object({
  email: emailSchema,
  password: passwordSchema,
  display_name: z.string().trim().max(120).optional(),
  accept: z.literal(true, { error: "Please accept the terms to continue." }),
});
export const resetSchema = z
  .object({ password: passwordSchema, confirm: z.string() })
  .refine((v) => v.password === v.confirm, { message: "Passwords don't match.", path: ["confirm"] });

const NAME_RE = /^[A-Za-z0-9](?:[A-Za-z0-9 _.-]{0,62}[A-Za-z0-9])?$/;
export const resourceName = z.string().trim().min(1, "Enter a name.").max(64, "Use at most 64 characters.").regex(NAME_RE, "Use letters, numbers, spaces, dots, dashes or underscores.");

export const projectSchema = z.object({
  name: resourceName,
  environment: z.enum(["development", "staging", "production"]),
  description: z.string().max(500).optional(),
});

export const pgName = z
  .string()
  .regex(/^[a-z][a-z0-9_]{0,62}$/, "Lowercase letters, digits and underscores, starting with a letter.")
  .refine((v) => !["postgres", "template0", "template1"].includes(v), "This name is reserved.");

export const databaseSchema = z.object({
  project_id: z.string().uuid("Choose a project."),
  name: resourceName,
  database_name: z.union([pgName, z.literal("")]).optional(),
  postgres_version: z.string().min(1),
  compute_profile: z.string().min(1),
  storage_mb: z.number().int().min(256, "At least 256 MB."),
  environment: z.enum(["development", "staging", "production"]),
});

export function slugPgName(name: string): string {
  let s = name.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
  if (!s || !/^[a-z]/.test(s)) s = `db_${s}`;
  s = s.slice(0, 63).replace(/_+$/, "") || "app";
  if (["postgres", "template0", "template1"].includes(s)) s = `${s}_db`;
  return s;
}
