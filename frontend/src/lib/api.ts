import { z } from "zod";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
const failureSchema = z.object({
  detail: z
    .union([
      z.string(),
      z.object({
        message: z.string().optional(),
        code: z.string().optional(),
      }),
      z.array(z.object({ loc: z.array(z.union([z.string(), z.number()])), msg: z.string() })),
    ])
    .optional(),
  error: z.object({ message: z.string(), category: z.string() }).optional(),
});

async function parseResponse<T>(response: Response, schema: z.ZodType<T>): Promise<T> {
  if (!response.ok) {
    const raw: unknown = await response.json().catch(() => null);
    const parsed = failureSchema.safeParse(raw);
    let message = `请求失败（${response.status}）`;
    if (parsed.success) {
      const { detail, error } = parsed.data;
      message = error
        ? error.message
        : typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((item) => `${item.loc.join(".")}：${item.msg}`).join("；") || message
            : detail?.message || message;
    }
    throw new ApiError(response.status, message);
  }
  const data: unknown = response.status === 204 ? null : await response.json();
  return schema.parse(data);
}

export async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: options.method ?? "GET",
    signal: options.signal,
    headers: options.body === undefined ? undefined : { "Content-Type": "application/json" },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  return parseResponse(response, schema);
}

export async function upload<T>(
  path: string,
  schema: z.ZodType<T>,
  file: File,
  method = "PUT",
  contentType?: string,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": contentType || file.type || "application/octet-stream" },
    body: file,
  });
  return parseResponse(response, schema);
}
export const remove = (path: string) => request(path, z.null(), { method: "DELETE" });
export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请重试。";
}
