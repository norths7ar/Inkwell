import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, onTestFinished, vi } from "vitest";
import { Settings } from "../features/Settings";

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
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
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
  await user.keyboard("{ArrowDown}{Home}{Enter}");
  expect(selector).toHaveTextContent("继承提供商默认协议");
  await user.click(screen.getByRole("button", { name: "从缓存补全" }));
  await act(async () =>
    pending.shift()?.(Response.json({ found: true, recommended_protocol: "anthropic-messages" })),
  );
  expect(selector).toHaveTextContent("继承提供商默认协议");
});
