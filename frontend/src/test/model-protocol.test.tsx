import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, onTestFinished, vi } from "vitest";
import { Settings } from "../features/Settings";
import { modelSchema } from "../lib/schema";

it("缓存查询期间用户选择的协议及显式继承不被返回结果覆盖", async () => {
  const user = userEvent.setup();
  const scroll = Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView");
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  onTestFinished(() => {
    if (scroll) Object.defineProperty(Element.prototype, "scrollIntoView", scroll);
    else Reflect.deleteProperty(Element.prototype, "scrollIntoView");
  });
  const pending: Array<(response: Response) => void> = [];
  const savedBodies: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === "/api/providers/provider/models") {
        savedBodies.push(String(init?.body));
        return Response.json(
          modelSchema.parse({
            id: "created",
            provider_id: "provider",
            model_id: "model",
            display_name: "手填名称",
            protocol_mode: "inherit",
            protocol_override: null,
            context_window: 32000,
            max_output_tokens: 2000,
            capabilities_json: "{}",
            enabled: true,
          }),
        );
      }
      if (url.startsWith("/api/model-catalog/lookup?")) {
        return new Promise<Response>((resolve) => pending.push(resolve));
      }
      if (url === "/api/providers") {
        return Response.json([
          {
            id: "provider",
            name: "Provider",
            preset: "opencode-zen",
            base_url: "https://mock.invalid",
            discovery_url: "",
            default_protocol: "openai-chat",
            api_key_configured: true,
            headers_configured: false,
            enabled: true,
          },
        ]);
      }
      if (["/api/models", "/api/provider-presets", "/api/settings/prompts"].includes(url)) {
        return Response.json([]);
      }
      if (url === "/api/settings") {
        return Response.json({
          default_model_id: null,
          background_model_id: null,
          global_rules_markdown: "",
        });
      }
      if (url === "/api/model-catalog") {
        return Response.json({
          cached: true,
          fetched_at: null,
          stale: false,
          stale_after_days: 14,
          provider_count: 1,
          model_count: 1,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<Settings open onOpenChange={() => {}} onSaved={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "模型" }));
  await user.click(await screen.findByRole("button", { name: "手动添加" }));
  await user.type(screen.getByLabelText("模型 ID"), "model");
  const selector = screen.getByRole("combobox", { name: "单模型协议覆盖" });
  await user.click(await screen.findByRole("button", { name: "从缓存补全" }));
  await user.type(screen.getByLabelText("显示名称"), "手填名称");
  await user.type(screen.getByLabelText("上下文窗口（可选）"), "32000");
  await user.type(screen.getByLabelText("最大输出 tokens（可选）"), "2000");
  selector.focus();
  await user.keyboard("o");
  expect(selector).toHaveTextContent("openai-chat");
  await act(async () =>
    pending.shift()?.(
      Response.json({
        found: true,
        recommended_protocol: "anthropic-messages",
        display_name: "缓存名称",
        context_window: 128000,
        max_output_tokens: 8000,
      }),
    ),
  );
  expect(selector).toHaveTextContent("openai-chat");
  expect(screen.getByLabelText("显示名称")).toHaveValue("手填名称");
  expect(screen.getByLabelText("上下文窗口（可选）")).toHaveValue(32000);
  expect(screen.getByLabelText("最大输出 tokens（可选）")).toHaveValue(2000);
  selector.focus();
  await user.keyboard("{ArrowDown}{Home}{ArrowDown}{Enter}");
  expect(selector).toHaveTextContent("继承提供商默认协议");
  await user.click(screen.getByRole("button", { name: "从缓存补全" }));
  await act(async () =>
    pending.shift()?.(Response.json({ found: true, recommended_protocol: "anthropic-messages" })),
  );
  expect(selector).toHaveTextContent("继承提供商默认协议");
  await user.click(screen.getByRole("button", { name: "保存模型" }));
  expect(savedBodies).toEqual([
    JSON.stringify({
      model_id: "model",
      display_name: "手填名称",
      protocol_mode: "inherit",
      protocol_override: null,
      context_window: 32000,
      max_output_tokens: 2000,
      enabled: true,
    }),
  ]);
});

it("缓存建议以自动来源保存，编辑名称只提交名称与启用状态", async () => {
  const user = userEvent.setup();
  const writes: Array<{ method: string | undefined; body: string }> = [];
  const model = modelSchema.parse({
    id: "created",
    provider_id: "provider",
    model_id: "model",
    display_name: "缓存名称",
    protocol_mode: "auto",
    protocol_override: null,
    context_window: null,
    max_output_tokens: null,
    capabilities_json: "{}",
    enabled: true,
    effective_context_window: 128000,
    upstream_context_window: 128000,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === "/api/providers/provider/models" || url === "/api/models/created") {
        writes.push({ method: init?.method, body: String(init?.body) });
        return Response.json(model);
      }
      if (url === "/api/providers")
        return Response.json([
          {
            id: "provider",
            name: "Provider",
            preset: "opencode-zen",
            base_url: "https://mock.invalid",
            discovery_url: "",
            default_protocol: "openai-chat",
            api_key_configured: true,
            headers_configured: false,
            enabled: true,
          },
        ]);
      if (["/api/models", "/api/provider-presets", "/api/settings/prompts"].includes(url))
        return Response.json([]);
      if (url === "/api/settings")
        return Response.json({
          default_model_id: null,
          background_model_id: null,
          global_rules_markdown: "",
        });
      if (url === "/api/model-catalog")
        return Response.json({
          cached: true,
          fetched_at: null,
          stale: false,
          stale_after_days: 14,
          provider_count: 1,
          model_count: 1,
        });
      if (url.startsWith("/api/model-catalog/lookup?"))
        return Response.json({
          found: true,
          display_name: "缓存名称",
          context_window: 128000,
          max_output_tokens: 8000,
          recommended_protocol: "anthropic-messages",
        });
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<Settings open onOpenChange={() => {}} onSaved={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "模型" }));
  await user.click(await screen.findByRole("button", { name: "手动添加" }));
  await user.type(screen.getByLabelText("模型 ID"), "model");
  await user.click(screen.getByRole("button", { name: "从缓存补全" }));
  expect(await screen.findByPlaceholderText("自动：128000")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "单模型协议覆盖" })).toHaveTextContent(
    "自动（anthropic-messages）",
  );
  await user.click(screen.getByRole("button", { name: "保存模型" }));
  expect(writes[0]).toEqual({
    method: "POST",
    body: JSON.stringify({
      model_id: "model",
      display_name: "缓存名称",
      protocol_mode: "auto",
      protocol_override: null,
      context_window: null,
      max_output_tokens: null,
      enabled: true,
    }),
  });
  await user.click(await screen.findByRole("button", { name: "编辑模型 model" }));
  await user.clear(screen.getByLabelText("显示名称"));
  await user.type(screen.getByLabelText("显示名称"), "新名称");
  await user.click(screen.getByRole("button", { name: "保存模型" }));
  expect(writes[1]).toEqual({
    method: "PATCH",
    body: JSON.stringify({ display_name: "新名称", enabled: true }),
  });
});

it("用户覆盖字段在上游变更时展示提醒并支持采用最新", async () => {
  const user = userEvent.setup();
  const resolvedCalls: Array<{ url: string; body: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === "/api/providers") {
        return Response.json([
          {
            id: "provider",
            name: "OpenCode Zen",
            preset: "opencode-zen",
            base_url: "https://mock.invalid",
            discovery_url: "",
            default_protocol: "openai-chat",
            api_key_configured: true,
            headers_configured: false,
            enabled: true,
          },
        ]);
      }
      if (url === "/api/models") {
        return Response.json([
          {
            id: "m1",
            provider_id: "provider",
            model_id: "claude-sonnet-4-5",
            display_name: "Claude Sonnet 4.5",
            protocol_override: null,
            context_window: 100000,
            max_output_tokens: null,
            capabilities_json: "{}",
            enabled: true,
            effective_protocol: "anthropic-messages",
            effective_protocol_override: "anthropic-messages",
            protocol_source: "catalog",
            effective_context_window: 100000,
            context_window_source: "user",
            effective_max_output_tokens: 64000,
            max_output_tokens_source: "catalog",
            upstream_protocol: "anthropic-messages",
            upstream_protocol_source: "catalog",
            upstream_context_window: 500000,
            upstream_context_window_source: "catalog",
            upstream_max_output_tokens: 64000,
            upstream_max_output_tokens_source: "catalog",
            pending_changes: [
              {
                field: "context_window",
                user_value: 100000,
                upstream_value: 500000,
                upstream_source: "catalog",
              },
            ],
          },
        ]);
      }
      if (url === "/api/models/m1/resolve-change") {
        resolvedCalls.push({ url, body: String(init?.body ?? "") });
        return Response.json({
          id: "m1",
          provider_id: "provider",
          model_id: "claude-sonnet-4-5",
          display_name: "Claude Sonnet 4.5",
          protocol_override: null,
          context_window: null,
          max_output_tokens: null,
          capabilities_json: "{}",
          enabled: true,
          effective_protocol: "anthropic-messages",
          effective_protocol_override: "anthropic-messages",
          protocol_source: "catalog",
          effective_context_window: 500000,
          context_window_source: "catalog",
          effective_max_output_tokens: 64000,
          max_output_tokens_source: "catalog",
          upstream_protocol: "anthropic-messages",
          upstream_protocol_source: "catalog",
          upstream_context_window: 500000,
          upstream_context_window_source: "catalog",
          upstream_max_output_tokens: 64000,
          upstream_max_output_tokens_source: "catalog",
          pending_changes: [],
        });
      }
      if (["/api/provider-presets", "/api/settings/prompts"].includes(url)) {
        return Response.json([]);
      }
      if (url === "/api/settings") {
        return Response.json({
          default_model_id: null,
          background_model_id: null,
          global_rules_markdown: "",
        });
      }
      if (url === "/api/model-catalog") {
        return Response.json({
          cached: true,
          fetched_at: null,
          stale: false,
          stale_after_days: 14,
          provider_count: 1,
          model_count: 1,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<Settings open onOpenChange={() => {}} onSaved={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "模型" }));
  expect(
    await screen.findByText(/上下文窗口：目录已更新为 500,000（当前手填 100,000）/),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "采用最新" }));
  expect(resolvedCalls).toEqual([
    {
      url: "/api/models/m1/resolve-change",
      body: JSON.stringify({
        field: "context_window",
        action: "accept",
        expected_upstream_value: 500000,
        expected_upstream_source: "catalog",
        expected_user_value: 100000,
      }),
    },
  ]);
  expect(await screen.findByText(/500,000 ctx/)).toBeInTheDocument();
});
