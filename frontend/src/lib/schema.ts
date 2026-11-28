import { z } from "zod";

export const modeSchema = z.enum(["discuss", "write", "review"]);
export const visibilitySchema = z.enum(["local", "global"]);
export const protocolSchema = z.enum([
  "openai-chat",
  "openai-responses",
  "anthropic-messages",
  "google-genai",
]);
export const bookSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  rules_markdown: z.string(),
  default_visibility: visibilitySchema,
  updated_at: z.string(),
  cover_path: z.string().default(""),
  chapter_count: z.number().default(0),
  word_count: z.number().default(0),
});
export const volumeSchema = z.object({
  id: z.string(),
  book_id: z.string(),
  title: z.string(),
  description: z.string(),
  sort_order: z.number(),
});
export const chapterSchema = z.object({
  id: z.string(),
  book_id: z.string(),
  volume_id: z.string().nullable(),
  title: z.string(),
  content: z.string(),
  summary: z.string(),
  word_count: z.number(),
  sort_order: z.number(),
  created_at: z.string().default(""),
  updated_at: z.string().default(""),
  deleted_at: z.string().nullable().default(null),
});
export const entryKindSchema = z.enum(["character", "world", "outline", "note"]);
export const entrySchema = z.object({
  id: z.string(),
  book_id: z.string(),
  kind: entryKindSchema,
  title: z.string(),
  category: z.string(),
  content: z.string(),
  visibility: visibilitySchema,
  meta_json: z.string(),
  sort_order: z.number(),
  created_at: z.string().default(""),
  updated_at: z.string().default(""),
  deleted_at: z.string().nullable().default(null),
});
export const entryFolderSchema = z.object({
  id: z.string(),
  book_id: z.string(),
  kind: entryKindSchema,
  title: z.string(),
  description: z.string().default(""),
  sort_order: z.number(),
  created_at: z.string().default(""),
  updated_at: z.string().default(""),
});
export const trashSchema = z.object({
  chapters: z.array(chapterSchema),
  entries: z.array(entrySchema),
});
export const pendingSchema = z.object({
  id: z.string(),
  book_id: z.string(),
  session_id: z.string().nullable(),
  run_id: z.string().nullable(),
  target_type: z.enum(["chapter", "entry"]),
  target_id: z.string().nullable(),
  action: z.enum(["create", "update", "delete"]),
  title: z.string(),
  before_content: z.string(),
  after_content: z.string(),
  status: z.enum(["pending", "applied", "rejected"]),
  created_at: z.string().optional(),
});
export const sessionSchema = z.object({
  id: z.string(),
  book_id: z.string(),
  title: z.string(),
  mode: modeSchema,
  visibility_scope: visibilitySchema,
  pinned: z.boolean().default(false),
  created_at: z.string().default(""),
  updated_at: z.string().default(""),
});
export const errorSchema = z.object({
  category: z.string(),
  message: z.string(),
  retryable: z.boolean(),
  status_code: z.number().nullable(),
  retry_after_seconds: z.number().nullable(),
});
export const runSchema = z.object({
  id: z.string(),
  session_id: z.string(),
  book_id: z.string(),
  status: z.enum(["running", "waiting_user", "completed", "cancelled", "failed"]),
  model_id: z.string().nullable(),
  pending_question_json: z.string().nullable(),
  error_message: z.string().nullable(),
  error: errorSchema.nullable(),
  input_tokens: z.number().default(0),
  output_tokens: z.number().default(0),
  cache_read_tokens: z.number().default(0),
  cache_write_tokens: z.number().default(0),
  reasoning_effort: z.string().nullable().default(null),
  reasoning_duration_ms: z.number().default(0),
  started_at: z.string(),
});
export const messageSchema = z.object({
  id: z.string(),
  session_id: z.string(),
  run_id: z.string().nullable(),
  role: z.enum(["system", "user", "assistant", "tool"]),
  seq: z.number(),
  blocks_json: z.string(),
});
export const blockSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("text"), text: z.string() }),
  z.object({ type: z.literal("reasoning"), text: z.string(), duration_ms: z.number().optional() }),
  z.object({
    type: z.literal("tool_call"),
    call_id: z.string(),
    name: z.string(),
    arguments_json: z.string(),
  }),
  z.object({
    type: z.literal("tool_result"),
    call_id: z.string(),
    name: z.string(),
    content: z.string(),
    is_error: z.boolean(),
  }),
  z.object({ type: z.literal("image"), media_type: z.string() }),
]);
export const providerSchema = z.object({
  id: z.string(),
  name: z.string(),
  preset: z.string(),
  base_url: z.string(),
  discovery_url: z.string(),
  default_protocol: protocolSchema,
  api_key_configured: z.boolean(),
  headers_configured: z.boolean(),
  enabled: z.boolean(),
});
export const paramSourceSchema = z.enum(["user", "server", "catalog", "default"]);
export const upstreamSourceSchema = z.enum(["server", "catalog"]);
export const upstreamChangeSchema = z.object({
  field: z.enum(["protocol", "context_window", "max_output_tokens"]),
  user_value: z.union([z.string(), z.number()]),
  upstream_value: z.union([z.string(), z.number()]),
  upstream_source: upstreamSourceSchema,
});
export const modelSchema = z.object({
  id: z.string(),
  provider_id: z.string(),
  model_id: z.string(),
  display_name: z.string(),
  protocol_override: protocolSchema.nullable(),
  protocol_mode: z.enum(["auto", "inherit", "override"]).optional(),
  context_window: z.number().nullable(),
  max_output_tokens: z.number().nullable(),
  capabilities_json: z.string(),
  enabled: z.boolean(),
  effective_protocol: protocolSchema.optional(),
  effective_protocol_override: protocolSchema.nullable().default(null),
  protocol_source: paramSourceSchema.default("default"),
  effective_context_window: z.number().nullable().default(null),
  context_window_source: paramSourceSchema.default("default"),
  effective_max_output_tokens: z.number().nullable().default(null),
  max_output_tokens_source: paramSourceSchema.default("default"),
  upstream_protocol: protocolSchema.nullable().default(null),
  upstream_protocol_source: upstreamSourceSchema.nullable().default(null),
  upstream_context_window: z.number().nullable().default(null),
  upstream_context_window_source: upstreamSourceSchema.nullable().default(null),
  upstream_max_output_tokens: z.number().nullable().default(null),
  upstream_max_output_tokens_source: upstreamSourceSchema.nullable().default(null),
  pending_changes: z.array(upstreamChangeSchema).default([]),
});
export const settingsSchema = z.object({
  default_model_id: z.string().nullable(),
  background_model_id: z.string().nullable(),
  global_rules_markdown: z.string(),
});
export const promptSchema = z.object({
  mode: modeSchema,
  markdown: z.string(),
  default_markdown: z.string(),
});
export const presetSchema = z.object({
  id: z.string(),
  name: z.string(),
  base_url: z.string(),
  default_protocol: protocolSchema,
  discovery_url: z.string(),
});
export const discoveredSchema = z.object({
  model_id: z.string(),
  display_name: z.string(),
  owned_by: z.string(),
  context_window: z.number().nullable().default(null),
  max_output_tokens: z.number().nullable().default(null),
  recommended_protocol: protocolSchema.nullable().default(null),
  supported: z.boolean().default(true),
  unsupported_reason: z.string().default(""),
  catalog_match: z.enum(["none", "provider", "cross_provider"]).default("none"),
  catalog_provider: z.string().nullable().default(null),
  catalog_stale: z.boolean().default(false),
  server_context_window: z.number().nullable().default(null),
  server_max_output_tokens: z.number().nullable().default(null),
  server_protocol: protocolSchema.nullable().default(null),
});
export const catalogStatusSchema = z.object({
  cached: z.boolean(),
  fetched_at: z.string().nullable(),
  stale: z.boolean(),
  stale_after_days: z.number(),
  provider_count: z.number(),
  model_count: z.number(),
  source: z.string().default("models.dev"),
});
export const catalogLookupSchema = z.object({
  found: z.boolean(),
  display_name: z.string().default(""),
  context_window: z.number().nullable().default(null),
  max_output_tokens: z.number().nullable().default(null),
  recommended_protocol: protocolSchema.nullable().default(null),
  match_scope: z.enum(["none", "provider", "cross_provider"]).default("none"),
  source_provider: z.string().nullable().default(null),
  stale: z.boolean().default(false),
});
export const searchSchema = z.object({
  target_type: z.enum(["chapter", "entry"]),
  target_id: z.string(),
  book_id: z.string(),
  title: z.string(),
  snippet: z.string(),
});
export const rollbackSchema = z.object({
  session_id: z.string(),
  message_id: z.string(),
  user_text: z.string().nullable(),
  deleted_messages: z.number(),
  deleted_runs: z.number(),
});
export const compactionSummarySchema = z.object({
  objective: z.string(),
  facts: z.array(z.string()),
  decisions: z.array(z.string()),
  open_questions: z.array(z.string()),
  next_steps: z.array(z.string()),
});
export const compactionSchema = z.object({
  id: z.string(),
  session_id: z.string(),
  through_seq: z.number(),
  summary: compactionSummarySchema,
  model_id: z.string().nullable(),
  input_tokens: z.number(),
  output_tokens: z.number(),
  created_at: z.string(),
});
export const batchChangesResultSchema = z.object({
  changes: z.array(pendingSchema),
  count: z.number(),
});
export const revisionSchema = z.object({
  id: z.string(),
  book_id: z.string(),
  target_type: z.enum(["chapter", "entry"]),
  target_id: z.string(),
  source: z.enum(["user", "pending_apply", "restore"]),
  pending_change_id: z.string().nullable(),
  title: z.string(),
  content: z.string(),
  snapshot_json: z.string(),
  created_at: z.string(),
});
export const restoreResultSchema = z.object({
  target: z.union([chapterSchema, entrySchema]),
  revision: revisionSchema,
});
export const backupRecordSchema = z.object({
  name: z.string(),
  size_bytes: z.number(),
  created_at: z.string(),
  kind: z.string().default("sqlite"),
  includes_attachments: z.boolean().default(false),
  includes_encryption_key: z.boolean().default(false),
});
export const systemStatusSchema = z.object({
  data_dir: z.string(),
  database_path: z.string(),
  database_bytes: z.number(),
  wal_bytes: z.number(),
  attachments_bytes: z.number(),
  backups_dir: z.string(),
  backups: z.array(backupRecordSchema),
  schema_version: z.number(),
  host: z.string(),
  port: z.number(),
  uptime_seconds: z.number(),
  books: z.number(),
  sessions: z.number(),
  running_runs: z.number(),
  waiting_runs: z.number(),
});
export type Book = z.infer<typeof bookSchema>;
export type Volume = z.infer<typeof volumeSchema>;
export type Chapter = z.infer<typeof chapterSchema>;
export type EntryKind = z.infer<typeof entryKindSchema>;
export type Entry = z.infer<typeof entrySchema>;
export type EntryFolder = z.infer<typeof entryFolderSchema>;
export type TrashPayload = z.infer<typeof trashSchema>;
export type PendingChange = z.infer<typeof pendingSchema>;
export type Session = z.infer<typeof sessionSchema>;
export type Run = z.infer<typeof runSchema>;
export type Message = z.infer<typeof messageSchema>;
export type Compaction = z.infer<typeof compactionSchema>;
export type Revision = z.infer<typeof revisionSchema>;
export type BackupRecord = z.infer<typeof backupRecordSchema>;
export type SystemStatus = z.infer<typeof systemStatusSchema>;
export type Provider = z.infer<typeof providerSchema>;
export type Model = z.infer<typeof modelSchema>;
export type CatalogStatus = z.infer<typeof catalogStatusSchema>;
export type CatalogLookup = z.infer<typeof catalogLookupSchema>;
export type Mode = z.infer<typeof modeSchema>;
export type Visibility = z.infer<typeof visibilitySchema>;
export type Protocol = z.infer<typeof protocolSchema>;

const ownership = { book_id: z.string(), session_id: z.string(), run_id: z.string() };
export const eventSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("content_changed"),
    book_id: z.string().nullable(),
    resource: z.string().optional(),
    resource_id: z.string().nullable().optional(),
    session_id: z.string().nullable().optional(),
    run_id: z.string().nullable().optional(),
  }),
  z.object({ type: z.literal("resync_required"), reason: z.string() }),
  z.object({ type: z.literal("run_started"), ...ownership }),
  z.object({ type: z.literal("text_delta"), ...ownership, delta: z.string() }),
  z.object({ type: z.literal("reasoning_delta"), ...ownership, delta: z.string() }),
  z.object({
    type: z.literal("reasoning_completed"),
    ...ownership,
    iteration: z.number(),
    block_index: z.number(),
    duration_ms: z.number(),
  }),
  z.object({
    type: z.literal("attempt_reset"),
    ...ownership,
    attempt: z.number(),
    reason: z.string(),
    error: errorSchema.nullable().optional(),
  }),
  z.object({
    type: z.literal("tool_start"),
    ...ownership,
    call_id: z.string(),
    name: z.string(),
    arguments_json: z.string(),
  }),
  z.object({
    type: z.literal("tool_result"),
    ...ownership,
    call_id: z.string(),
    name: z.string(),
    content: z.string(),
    is_error: z.boolean(),
  }),
  z.object({ type: z.literal("pending_change_created"), ...ownership, change: pendingSchema }),
  z.object({ type: z.literal("pending_change_updated"), ...ownership, change: pendingSchema }),
  z.object({
    type: z.literal("waiting_user"),
    ...ownership,
    question: z.string(),
    options: z.array(z.string()),
  }),
  z.object({
    type: z.literal("run_done"),
    ...ownership,
    status: runSchema.shape.status,
    error_message: z.string().nullable(),
    error: errorSchema.nullable().optional(),
  }),
]);
export type ServerEvent = z.infer<typeof eventSchema>;
