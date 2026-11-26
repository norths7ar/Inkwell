import { useState } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { Editor, type DocumentValue } from "../features/Editor";
import { PendingPanel } from "../features/PendingPanel";
import { ChatPanel } from "../features/ChatPanel";
import { request } from "../lib/api";
import { EventsProvider, useEvents } from "../lib/events";
import type { PendingChange } from "../lib/schema";

const documentSchema = z.object({ title: z.string(), content: z.string() });
const original = { title: "第一章", content: "旧正文" };

function EditingWorkflow() {
  const [value, setValue] = useState<DocumentValue>(original);
  async function save(next: DocumentValue) {
    const saved = await request("/chapters/chapter-1", documentSchema, {
      method: "PATCH",
      body: next,
    });
    setValue(saved);
  }
  return <Editor documentKey="chapter-1" value={value} kind="章节" onSave={save} />;
}

const change: PendingChange = {
  id: "change-1",
  book_id: "book-1",
  session_id: "session-1",
  run_id: "run-1",
  target_type: "chapter",
  target_id: "chapter-1",
  action: "update",
  title: "第一章",
  before_content: "旧正文",
  after_content: "建议正文",
  status: "pending",
};

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly listeners = new Map<string, (event: MessageEvent<string>) => void>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readonly close = vi.fn();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(name, listener);
  }

  emit(name: string, payload: unknown, id: string) {
    this.listeners.get(name)?.(
      new MessageEvent(name, { data: JSON.stringify(payload), lastEventId: id }),
    );
  }
}

function EventConsumer() {
  const [text, setText] = useState("");
  const [lastId, setLastId] = useState("");
  const [title, setTitle] = useState("");
  useEvents((event, id) => {
    setLastId(id);
    if (event.type === "text_delta") setText((previous) => previous + event.delta);
    if (event.type === "resync_required") {
      void request("/chapters/chapter-1", documentSchema).then((document) => {
        setTitle(document.title);
      });
    }
  });
  return (
    <div>
      <output aria-label="流式正文">{text}</output>
      <output aria-label="事件游标">{lastId}</output>
      <output aria-label="重新拉取的标题">{title}</output>
    </div>
  );
}

describe("日常写作核心闭环", () => {
  it("仅显式保存写入后端，暂存草稿可恢复并支持 Ctrl+S", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn<typeof fetch>(async (_url, options) => {
      if (options?.method !== "PATCH" || typeof options.body !== "string") {
        throw new Error("Unexpected request");
      }
      const body: unknown = JSON.parse(options.body);
      expect(body).not.toHaveProperty("base");
      return Response.json(documentSchema.parse(body));
    });
    vi.stubGlobal("fetch", fetchMock);
    const first = render(<EditingWorkflow />);
    await user.clear(screen.getByLabelText("正文 · Markdown"));
    await user.type(screen.getByLabelText("正文 · Markdown"), "未保存草稿");
    await screen.findByText("未保存（已暂存本地）");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(localStorage.getItem("inkwell:draft:chapter-1")).toContain("未保存草稿");
    first.unmount();

    render(<EditingWorkflow />);
    expect(screen.getByLabelText("正文 · Markdown")).toHaveValue("未保存草稿");
    expect(screen.getByText(/已恢复本地草稿/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(localStorage.getItem("inkwell:draft:chapter-1")).toBeNull());
    expect(screen.getByLabelText("正文 · Markdown")).toHaveValue("未保存草稿");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/chapters/chapter-1");

    await user.type(screen.getByLabelText("正文 · Markdown"), "，继续写作");
    await user.keyboard("{Control>}s{/Control}");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await screen.findByText("已保存", { exact: true });
    expect(screen.getByLabelText("正文 · Markdown")).toHaveValue("未保存草稿，继续写作");
  });

  it("待审应用冲突时保留待审项，成功应用后刷新列表", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockResolvedValueOnce(
      Response.json(
        { detail: "Pending change no longer matches current content" },
        { status: 409 },
      ),
    );
    fetchMock.mockResolvedValueOnce(Response.json({ ...change, status: "applied" }));
    vi.stubGlobal("fetch", fetchMock);
    const changed = vi.fn();
    function ReviewWorkflow() {
      const [changes, setChanges] = useState([change]);
      return (
        <PendingPanel
          changes={changes}
          focusedId={null}
          beforeApply={() => true}
          onChanged={() => {
            changed();
            setChanges([]);
          }}
        />
      );
    }
    render(<ReviewWorkflow />);
    expect(screen.getByText("旧正文")).toBeInTheDocument();
    expect(screen.getByText("建议正文")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "应用变更" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("正文基线已变化");
    expect(screen.getByRole("alert")).toHaveTextContent("待审项已保留");
    expect(changed).not.toHaveBeenCalled();
    expect(screen.getByText("建议正文")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "应用变更" }));
    expect(await screen.findByText("暂无待审变更")).toBeInTheDocument();
    expect(changed).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/pending-changes/change-1/apply",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("共享单条 SSE 流，保留大整数游标字符串并响应 resync 重拉", async () => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    let refreshed = false;
    const fetchMock = vi.fn<typeof fetch>(async (url) => {
      if (url === "/api/books/book-1/sessions") {
        return Response.json([
          {
            id: "session-1",
            book_id: "book-1",
            title: "创作会话",
            mode: "write",
            visibility_scope: "local",
          },
        ]);
      }
      if (url === "/api/models" || url === "/api/sessions/session-1/runs") return Response.json([]);
      if (url === "/api/settings")
        return Response.json({
          default_model_id: null,
          background_model_id: null,
          global_rules_markdown: "",
        });
      if (url === "/api/sessions/session-1/messages") {
        return Response.json(
          refreshed
            ? [
                {
                  id: "message-1",
                  session_id: "session-1",
                  run_id: "run-1",
                  role: "assistant",
                  seq: 1,
                  blocks_json: JSON.stringify([{ type: "text", text: "重同步后的完整正文" }]),
                },
              ]
            : [],
        );
      }
      if (url === "/api/chapters/chapter-1")
        return Response.json({ title: "服务端更新后的章节", content: "正文" });
      throw new Error(`Unexpected request: ${String(url)}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const mounted = render(
      <EventsProvider>
        <EventConsumer />
        <ChatPanel bookId="book-1" onPending={() => undefined} />
      </EventsProvider>,
    );
    await screen.findByText("暂无消息");
    expect(FakeEventSource.instances).toHaveLength(1);
    const source = FakeEventSource.instances[0];
    expect(source?.url).toBe("/api/events");
    if (!source) throw new Error("Expected a global EventSource connection");
    const ownership = { book_id: "book-1", session_id: "session-1", run_id: "run-1" };
    act(() => {
      source.emit("text_delta", { ...ownership, delta: "第一句" }, "1795700000000000001");
      source.emit("text_delta", { ...ownership, delta: "，第二句" }, "1795700000000000002");
    });
    expect(screen.getByLabelText("流式正文")).toHaveTextContent("第一句，第二句");
    expect(screen.getByLabelText("事件游标")).toHaveTextContent("1795700000000000002");
    expect(screen.getByLabelText("正在生成")).toHaveTextContent("第一句，第二句");
    mounted.rerender(
      <EventsProvider>
        <EventConsumer />
        <ChatPanel bookId="book-1" onPending={() => undefined} expanded />
      </EventsProvider>,
    );
    expect(screen.getByLabelText("正在生成")).toHaveTextContent("第一句，第二句");
    expect(FakeEventSource.instances).toHaveLength(1);
    const historyCallsBefore = fetchMock.mock.calls.filter(
      ([url]) => url === "/api/sessions/session-1/messages",
    ).length;
    refreshed = true;
    act(() =>
      source.emit("resync_required", { reason: "replay_unavailable" }, "1795700000000000003"),
    );
    await waitFor(() => {
      expect(screen.getByLabelText("重新拉取的标题")).toHaveTextContent("服务端更新后的章节");
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/chapters/chapter-1", expect.any(Object));
    expect(await screen.findByText("重同步后的完整正文")).toBeInTheDocument();
    expect(screen.queryByLabelText("正在生成")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([url]) => url === "/api/sessions/session-1/messages").length,
    ).toBeGreaterThan(historyCallsBefore);
    expect(FakeEventSource.instances).toHaveLength(1);
    mounted.unmount();
    expect(source.close).toHaveBeenCalledOnce();
  });
});
