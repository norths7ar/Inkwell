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
      z.array(z.object({ loc: z.array(z.union([z.string(), z.number()])), msg: z.string() })),
    ])
    .optional(),
  error: z.object({ message: z.string(), category: z.string() }).optional(),
});

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
  if (!response.ok) {
    const raw: unknown = await response.json().catch(() => null);
    const parsed = failureSchema.safeParse(raw);
    let message = `请求失败（${response.status}）`;
    if (parsed.success) {
      const { detail, error } = parsed.data;
      message = error
        ? `${error.message}（${error.category}）`
        : typeof detail === "string"
          ? detail
          : detail?.map((item) => `${item.loc.join(".")}：${item.msg}`).join("；") || message;
    }
    throw new ApiError(response.status, message);
  }
  const data: unknown = response.status === 204 ? null : await response.json();
  return schema.parse(data);
}
export const remove = (path: string) => request(path, z.null(), { method: "DELETE" });
export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请重试。";
}
