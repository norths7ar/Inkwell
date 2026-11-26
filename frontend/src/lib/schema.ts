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
});
export const entrySchema = z.object({
  id: z.string(),
  book_id: z.string(),
  kind: z.enum(["character", "world", "note"]),
  title: z.string(),
  category: z.string(),
  content: z.string(),
  visibility: visibilitySchema,
  meta_json: z.string(),
  sort_order: z.number(),
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
  z.object({ type: z.literal("reasoning"), text: z.string() }),
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
export const modelSchema = z.object({
  id: z.string(),
  provider_id: z.string(),
  model_id: z.string(),
  display_name: z.string(),
  protocol_override: protocolSchema.nullable(),
  context_window: z.number().nullable(),
  max_output_tokens: z.number().nullable(),
  capabilities_json: z.string(),
  enabled: z.boolean(),
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
});
export const searchSchema = z.object({
  target_type: z.enum(["chapter", "entry"]),
  target_id: z.string(),
  book_id: z.string(),
  title: z.string(),
  snippet: z.string(),
});
export type Book = z.infer<typeof bookSchema>;
export type Volume = z.infer<typeof volumeSchema>;
export type Chapter = z.infer<typeof chapterSchema>;
export type Entry = z.infer<typeof entrySchema>;
export type PendingChange = z.infer<typeof pendingSchema>;
export type Session = z.infer<typeof sessionSchema>;
export type Run = z.infer<typeof runSchema>;
export type Message = z.infer<typeof messageSchema>;
export type Provider = z.infer<typeof providerSchema>;
export type Model = z.infer<typeof modelSchema>;
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
