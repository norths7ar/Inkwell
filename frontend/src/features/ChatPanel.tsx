import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  ArrowUp,
  BookOpen,
  Check,
  Globe,
  Lightbulb,
  Lock,
  MessageSquarePlus,
  Pencil,
  PenLine,
  SearchCheck,
  Sparkles,
  Square,
  Trash2,
} from "lucide-react";
import { z } from "zod";
import {
  Alert,
  Badge,
  Button,
  Dialog,
  EmptyState,
  Input,
  Menu,
  MenuItem,
  MenuSeparator,
  Select,
  Textarea,
} from "../components/ui";
import { errorText, remove, request } from "../lib/api";
import { useConnection, useEvents } from "../lib/events";
import {
  blockSchema,
  messageSchema,
  modelSchema,
  modeSchema,
  runSchema,
  sessionSchema,
  settingsSchema,
  type Message,
  type Model,
  type Run,
  type ServerEvent,
  type Session,
} from "../lib/schema";
import "./chat.css";

const modes = [
  { value: "discuss", label: "讨论" },
  { value: "write", label: "写作" },
  { value: "review", label: "审阅" },
];
const questionSchema = z.object({ question: z.string(), options: z.array(z.string()) });
const toolNames: Readonly<Record<string, string>> = {
  list_chapters: "浏览章节目录",
  read_chapter: "读取章节",
  read_summaries: "读取章节摘要",
  read_entries: "查阅设定",
  search_content: "搜索作品",
  propose_entry_edit: "提出设定修改",
  propose_chapter_edit: "提出章节修改",
  ask_user: "请求你的决定",
};
const fieldNames: Readonly<Record<string, string>> = {
  title: "标题",
  content: "正文",
  summary: "摘要",
  query: "关键词",
  question: "问题",
  options: "选项",
  message: "说明",
  error: "错误",
  code: "错误类型",
  data: "结果",
  chapters: "章节",
  entries: "设定",
  results: "搜索结果",
  hits: "匹配结果",
  kind: "类型",
  action: "操作",
  before_content: "修改前",
  after_content: "修改后",
  status: "状态",
  visibility: "可见范围",
  category: "分类",
  count: "数量",
  text: "内容",
  snippet: "片段",
  target_type: "目标",
  target_id: "目标编号",
  chapter_id: "章节编号",
  entry_id: "设定编号",
  book_id: "作品编号",
  limit: "数量上限",
  offset: "起始位置",
  pending_change: "待审变更",
  change: "待审变更",
  pending_change_id: "待审编号",
  ok: "成功",
};

function toolName(name: string) {
  return toolNames[name] || name.replaceAll("_", " ");
}

function readableValue(value: unknown, depth = 0): string {
  if (value === null) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) {
    const values: unknown[] = value;
    return values.map((item) => readableValue(item, depth + 1)).join("\n\n") || "无结果";
  }
  if (typeof value === "object") {
    if (depth > 5) return "详细结果已记录";
    return (
      Object.entries(value)
        .filter(
          ([key]) => !["id", "run_id", "session_id", "created_at", "updated_at"].includes(key),
        )
        .map(
          ([key, item]: [string, unknown]) =>
            `${fieldNames[key] || key.replaceAll("_", " ")}：${readableValue(item, depth + 1)}`,
        )
        .join("\n") || "已完成"
    );
  }
  return "";
}

function toolSummary(content: string) {
  try {
    const value: unknown = JSON.parse(content);
    return readableValue(value);
  } catch {
    return content;
  }
}

function parseBlocks(message: Message) {
  try {
    const raw: unknown = JSON.parse(message.blocks_json);
    return z.array(blockSchema).parse(raw);
  } catch {
    return null;
  }
}

function MessageContent({ message }: { message: Message }) {
  const blocks = parseBlocks(message);
  if (!blocks) return <Alert>这条历史消息格式无法识别。</Alert>;
  return (
    <article className={`message message-${message.role}`}>
      <div className="message-label">
        {message.role === "user" ? "你" : message.role === "tool" ? "工具结果" : "墨匣"}
      </div>
      {blocks.map((block, index) => {
        if (block.type === "text")
          return (
            <div className="message-text" key={index}>
              {block.text}
            </div>
          );
        if (block.type === "reasoning")
          return (
            <details className="reasoning" key={index}>
              <summary>思考过程</summary>
              <div className="message-text muted">{block.text}</div>
            </details>
          );
        if (block.type === "tool_call")
          return (
            <details className="tool-state" key={block.call_id}>
              <summary>{toolName(block.name)}</summary>
              <div className="tool-summary">{toolSummary(block.arguments_json)}</div>
            </details>
          );
        if (block.type === "tool_result")
          return (
            <details className="tool-state" key={block.call_id}>
              <summary>
                {block.is_error ? "失败" : "已完成"} · {toolName(block.name)}
              </summary>
              <div className="tool-summary">{toolSummary(block.content)}</div>
            </details>
          );
        return (
          <div className="muted" key={index}>
            图片消息（{block.media_type}）
          </div>
        );
      })}
    </article>
  );
}

type ToolState = { id: string; name: string; status: "running" | "done" | "error" };
type LiveTurn = { runId: string; text: string; reasoning: string; tools: ToolState[] };
type LiveQuestion = { runId: string; question: string; options: string[] };
const emptyTurn = (): LiveTurn => ({ runId: "", text: "", reasoning: "", tools: [] });

function SessionChat({
  session,
  models,
  defaultModelId,
  onUpdated,
  onPending,
}: {
  session: Session;
  models: Model[];
  defaultModelId: string | null;
  onUpdated: (session: Session) => void;
  onPending: (id: string) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [live, setLive] = useState<LiveTurn>(emptyTurn);
  const [text, setText] = useState("");
  const [answer, setAnswer] = useState("");
  const [modelId, setModelId] = useState("default");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmGlobal, setConfirmGlobal] = useState(false);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<{ id: string; title: string }[]>([]);
  const mounted = useRef(true);
  const fetchSequence = useRef(0);
  const eventVersion = useRef(0);
  const [eventRunning, setEventRunning] = useState(false);
  const [liveQuestion, setLiveQuestion] = useState<LiveQuestion | null>(null);
  const [finishedIds, setFinishedIds] = useState<string[]>([]);
  const historyElement = useRef<HTMLDivElement>(null);
  const followOutput = useRef(true);

  useEffect(() => {
    const element = historyElement.current;
    if (element && followOutput.current) element.scrollTop = element.scrollHeight;
  }, [messages, live, pending, notice]);

  const refresh = useCallback(async () => {
    const sequence = ++fetchSequence.current;
    const version = eventVersion.current;
    try {
      const [history, records] = await Promise.all([
        request(`/sessions/${session.id}/messages`, z.array(messageSchema)),
        request(`/sessions/${session.id}/runs`, z.array(runSchema)),
      ]);
      if (!mounted.current || sequence !== fetchSequence.current) return;
      setMessages(history);
      setRuns(records);
      setLive((old) =>
        records.some((run) => run.id === old.runId && run.status !== "running") ? emptyTurn() : old,
      );
      if (version === eventVersion.current)
        setEventRunning(records.some((run) => run.status === "running"));
      setLoading(false);
    } catch (reason) {
      if (mounted.current && sequence === fetchSequence.current) {
        setError(errorText(reason));
        setLoading(false);
      }
    }
  }, [session.id]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
      fetchSequence.current += 1;
    };
  }, [refresh]);

  const receive = useCallback(
    (event: ServerEvent) => {
      if (event.type === "resync_required") {
        setLive(emptyTurn());
        setLiveQuestion(null);
        setNotice("");
        void refresh();
        return;
      }
      if (event.type === "content_changed") {
        if (event.session_id === session.id) void refresh();
        return;
      }
      if (event.session_id !== session.id) return;
      eventVersion.current += 1;
      if (event.type === "run_started") {
        setEventRunning(true);
        setLiveQuestion(null);
        setNotice("");
        setLive({ ...emptyTurn(), runId: event.run_id });
        void refresh();
      } else if (event.type === "text_delta" || event.type === "reasoning_delta") {
        setEventRunning(true);
        setLive((old) => {
          const next = old.runId === event.run_id ? old : { ...emptyTurn(), runId: event.run_id };
          return event.type === "text_delta"
            ? { ...next, text: next.text + event.delta }
            : { ...next, reasoning: next.reasoning + event.delta };
        });
      } else if (event.type === "attempt_reset") {
        setLive({ ...emptyTurn(), runId: event.run_id });
        setNotice(`第 ${event.attempt} 次重试：${event.reason}`);
      } else if (event.type === "tool_start") {
        // The assistant message is persisted before tool_start. Its streamed copy
        // must be removed when loading that message, before the next model turn.
        setLive((old) => ({
          runId: event.run_id,
          text: "",
          reasoning: "",
          tools: [
            ...old.tools.filter((tool) => tool.id !== event.call_id),
            { id: event.call_id, name: event.name, status: "running" },
          ],
        }));
        void refresh();
      } else if (event.type === "tool_result") {
        setLive((old) => ({
          ...old,
          tools: old.tools.map((tool) =>
            tool.id === event.call_id
              ? { ...tool, status: event.is_error ? "error" : "done" }
              : tool,
          ),
        }));
      } else if (event.type === "pending_change_created") {
        setPending((old) => [
          ...old.filter((item) => item.id !== event.change.id),
          { id: event.change.id, title: event.change.title || "待审变更" },
        ]);
      } else if (event.type === "waiting_user" || event.type === "run_done") {
        setEventRunning(false);
        if (event.type === "waiting_user") {
          setLiveQuestion({
            runId: event.run_id,
            question: event.question,
            options: event.options,
          });
        } else {
          setLiveQuestion(null);
          setFinishedIds((old) => [...old.filter((id) => id !== event.run_id), event.run_id]);
          if (event.error) setNotice(`${event.error.message}（${event.error.category}）`);
        }
        void refresh();
      }
    },
    [refresh, session.id],
  );
  useEvents(receive);

  const active = runs.find(
    (run) =>
      !finishedIds.includes(run.id) && (run.status === "running" || run.status === "waiting_user"),
  );
  const latest = runs[0];
  const persistedWaiting = active?.status === "waiting_user" ? active : undefined;
  const waiting = liveQuestion?.runId || persistedWaiting?.id;
  const running = !waiting && (eventRunning || active?.status === "running");
  const locked = Boolean(active) || Boolean(waiting) || eventRunning || busy;
  let question: z.infer<typeof questionSchema> | null = liveQuestion;
  if (!question && persistedWaiting?.pending_question_json) {
    try {
      const raw: unknown = JSON.parse(persistedWaiting.pending_question_json);
      question = questionSchema.parse(raw);
    } catch {
      /* The persisted run remains cancellable if its question is malformed. */
    }
  }
  const effectiveModel = modelId === "default" ? defaultModelId : modelId;
  const availableModel = models.some((model) => model.id === effectiveModel && model.enabled);

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (reason) {
      if (mounted.current) setError(errorText(reason));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function send() {
    if (!text.trim() || locked) return;
    const sent = text;
    await perform(async () => {
      const run = await request(`/sessions/${session.id}/runs`, runSchema, {
        method: "POST",
        body: { text: sent, model_id: modelId === "default" ? null : modelId },
      });
      if (!mounted.current) return;
      setText((current) => (current === sent ? "" : current));
      setRuns((old) => [run, ...old.filter((item) => item.id !== run.id)]);
      await refresh();
    });
  }

  async function reply() {
    if (!waiting || !answer.trim()) return;
    const sent = answer;
    await perform(async () => {
      const run = await request(`/runs/${waiting}/answer`, runSchema, {
        method: "POST",
        body: { text: sent, ...(modelId === "default" ? {} : { model_id: modelId }) },
      });
      if (!mounted.current) return;
      setLiveQuestion(null);
      setAnswer((current) => (current === sent ? "" : current));
      setRuns((old) => [run, ...old.filter((item) => item.id !== waiting && item.id !== run.id)]);
      await refresh();
    });
  }

  return (
    <>
      <Alert>{error}</Alert>
      {error && (
        <Button
          variant="ghost"
          onClick={() => {
            setError("");
            void refresh();
          }}
        >
          重新载入会话
        </Button>
      )}
      <div
        className="chat-history"
        aria-label="对话历史"
        ref={historyElement}
        onScroll={(event) => {
          const element = event.currentTarget;
          followOutput.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < 80;
        }}
      >
        {loading ? (
          <p className="muted">正在载入会话…</p>
        ) : (
          messages.length === 0 && <EmptyState title="暂无消息" />
        )}
        {messages.map((message) => (
          <MessageContent key={message.id} message={message} />
        ))}
        {(live.text || live.reasoning) && (
          <article className="message stream" aria-label="正在生成">
            <div className="message-label">墨匣 · {running ? "生成中" : "同步中"}</div>
            {live.reasoning && (
              <details className="reasoning">
                <summary>思考过程</summary>
                <div className="message-text muted">{live.reasoning}</div>
              </details>
            )}
            {live.text && <div className="message-text">{live.text}</div>}
          </article>
        )}
        {live.tools.some((tool) => tool.status === "running") && (
          <div className="tool-state">
            {live.tools
              .filter((tool) => tool.status === "running")
              .map((tool) => (
                <div key={tool.id}>
                  <Badge>执行中</Badge> {toolName(tool.name)}
                </div>
              ))}
          </div>
        )}
        {pending.length > 0 && (
          <div className="stack">
            <span className="section-title">本次待审建议</span>
            {pending.map((item) => (
              <Button key={item.id} onClick={() => onPending(item.id)}>
                {item.title} · 查看变更
              </Button>
            ))}
          </div>
        )}
        {notice && !(latest?.status === "failed" && latest.error) && (
          <p className="chat-notice" role="status">
            {notice}
          </p>
        )}
        {latest?.status === "failed" && latest.error && (
          <Alert>
            {latest.error.message}（{latest.error.category}）
            {latest.error.retry_after_seconds !== null &&
              ` 建议 ${latest.error.retry_after_seconds} 秒后重试。`}
          </Alert>
        )}
        {waiting && (
          <section className="question-card stack" aria-label="等待你的回答">
            <Badge tone="warning">需要你的决定</Badge>
            <p>{question?.question || "这次运行正在等待回答。"}</p>
            {question && question.options.length > 0 && (
              <div className="row">
                {question.options.map((option, index) => (
                  <Button key={index} onClick={() => setAnswer(option)}>
                    {option}
                  </Button>
                ))}
              </div>
            )}
            <Textarea
              label="你的回答"
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              rows={3}
            />
            <Button
              variant="primary"
              disabled={busy || !answer.trim()}
              onClick={() => {
                void reply();
              }}
            >
              回答并继续
            </Button>
          </section>
        )}
      </div>
      <div className="chat-composer-area">
        {(waiting || running) && (
          <div className="chat-run-status">
            <span>{waiting ? "等待回答" : "正在生成…"}</span>
            {(active || eventRunning || waiting) && (
              <Button
                variant="ghost"
                disabled={busy || (!active && !live.runId && !waiting)}
                onClick={() => {
                  const id = waiting || active?.id || live.runId;
                  if (!id) return;
                  void perform(async () => {
                    await request(`/runs/${id}/cancel`, runSchema, { method: "POST" });
                    setEventRunning(false);
                    setLiveQuestion(null);
                    setFinishedIds((old) => [...old.filter((item) => item !== id), id]);
                    setLive(emptyTurn());
                    await refresh();
                  });
                }}
              >
                <Square size={14} />
                取消运行
              </Button>
            )}
          </div>
        )}
        <div className="chat-composer">
          <Textarea
            aria-label={running ? "中途补充指令" : "写作指令"}
            placeholder={running ? "补充指令…" : "输入消息…"}
            value={text}
            onChange={(event) => setText(event.target.value)}
            disabled={Boolean(waiting)}
            rows={3}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !running) {
                event.preventDefault();
                void send();
              }
            }}
          />
          <div className="composer-footer">
            <div className="composer-options">
              <Select
                compact
                label="创作模式"
                value={session.mode}
                disabled={locked}
                icon={
                  session.mode === "discuss" ? (
                    <Lightbulb size={12} />
                  ) : session.mode === "write" ? (
                    <PenLine size={12} />
                  ) : (
                    <SearchCheck size={12} />
                  )
                }
                options={modes}
                onChange={(value) => {
                  void perform(async () =>
                    onUpdated(
                      await request(`/sessions/${session.id}`, sessionSchema, {
                        method: "PATCH",
                        body: { mode: modeSchema.parse(value) },
                      }),
                    ),
                  );
                }}
              />
              <Select
                compact
                label="协作模型"
                value={modelId}
                disabled={running || busy}
                icon={<Sparkles size={12} />}
                options={[
                  { value: "default", label: "默认模型" },
                  ...models
                    .filter((model) => model.enabled)
                    .map((model) => ({
                      value: model.id,
                      label: model.display_name || model.model_id,
                    })),
                ]}
                onChange={setModelId}
              />
            </div>
            <div className="composer-actions">
              {session.visibility_scope === "global" ? (
                <button
                  type="button"
                  className="composer-visibility-pill is-global"
                  disabled
                  title="当前会话已开启全局资料，不可退回仅公开"
                >
                  <Globe size={12} />
                  <span>全局</span>
                  <Lock size={10} />
                </button>
              ) : (
                <button
                  type="button"
                  className="composer-visibility-pill"
                  disabled={locked}
                  title="当前仅读取公开设定；点击可开启全局资料（不可逆）"
                  onClick={() => setConfirmGlobal(true)}
                >
                  <BookOpen size={12} />
                  <span>仅公开</span>
                </button>
              )}
              {running ? (
                <Button
                  variant="primary"
                  className="composer-send"
                  aria-label="插话"
                  title="插话"
                  disabled={busy || !text.trim()}
                  onClick={() => {
                    const id = active?.id || live.runId;
                    if (!id) return;
                    const sent = text;
                    void perform(async () => {
                      await request(
                        `/runs/${id}/steer`,
                        z.object({ run_id: z.string(), status: z.string() }),
                        { method: "POST", body: { text: sent } },
                      );
                      setText((current) => (current === sent ? "" : current));
                    });
                  }}
                >
                  <ArrowUp size={16} />
                </Button>
              ) : (
                <Button
                  variant="primary"
                  className="composer-send"
                  aria-label="发送"
                  title="发送 · Ctrl+Enter"
                  disabled={locked || !text.trim() || !availableModel}
                  onClick={() => {
                    void send();
                  }}
                >
                  <ArrowUp size={16} />
                </Button>
              )}
            </div>
          </div>
        </div>
        {!availableModel && !waiting && <p className="chat-model-warning">尚未配置可用模型</p>}
      </div>
      <Dialog
        title="开启全局资料"
        description="开启后，AI 在本会话中可查阅所有隐藏设定；由于对话上下文已包含隐藏内容，开启后无法退回「仅公开资料」。"
        open={confirmGlobal}
        onOpenChange={(open) => {
          if (!open && !busy) setConfirmGlobal(false);
        }}
      >
        <Alert>{error}</Alert>
        <div className="dialog-actions">
          <Button disabled={busy} onClick={() => setConfirmGlobal(false)}>
            取消
          </Button>
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => {
              void perform(async () => {
                const updated = await request(`/sessions/${session.id}`, sessionSchema, {
                  method: "PATCH",
                  body: { visibility_scope: "global" },
                });
                onUpdated(updated);
                setConfirmGlobal(false);
              });
            }}
          >
            {busy ? "开启中…" : "开启全局资料"}
          </Button>
        </div>
      </Dialog>
    </>
  );
}

type ChatPanelProps = {
  bookId: string;
  onPending: (id: string) => void;
  expanded?: boolean;
  toolbarStart?: ReactNode;
  sidebarTarget?: HTMLElement | null;
};

function BookChat({
  bookId,
  onPending,
  expanded = false,
  toolbarStart,
  sidebarTarget,
}: ChatPanelProps) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [models, setModels] = useState<Model[]>([]);
  const [defaultModelId, setDefaultModelId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<"new" | "rename" | "delete" | null>(null);
  const [editingId, setEditingId] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const sequence = useRef(0);
  const mounted = useRef(true);
  const connection = useConnection();

  const refresh = useCallback(async () => {
    const current = ++sequence.current;
    try {
      const [items, catalogue, settings] = await Promise.all([
        request(`/books/${bookId}/sessions`, z.array(sessionSchema)),
        request("/models", z.array(modelSchema)),
        request("/settings", settingsSchema),
      ]);
      if (!mounted.current || current !== sequence.current) return;
      setSessions(items);
      setSessionId((old) => (items.some((item) => item.id === old) ? old : items[0]?.id || ""));
      setModels(catalogue);
      setDefaultModelId(settings.default_model_id);
      setLoaded(true);
    } catch (reason) {
      if (mounted.current && current === sequence.current) {
        setError(errorText(reason));
        setLoaded(true);
      }
    }
  }, [bookId]);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
      sequence.current += 1;
    };
  }, [refresh]);
  useEvents(
    useCallback(
      (event: ServerEvent) => {
        if (
          event.type === "resync_required" ||
          (event.type === "content_changed" &&
            (event.book_id === null ||
              (event.book_id === bookId && event.session_id && !event.run_id)))
        )
          void refresh();
      },
      [bookId, refresh],
    ),
  );
  const selected = sessions.find((session) => session.id === sessionId);
  const editingSession = sessions.find((session) => session.id === editingId);
  function editSession(action: "rename" | "delete", session: Session) {
    setEditingId(session.id);
    setTitle(session.title);
    setEditing(action);
  }
  async function saveSession() {
    setBusy(true);
    setError("");
    try {
      if (editing !== "new" && !editingSession) throw new Error("会话已被删除。");
      if (editing === "delete" && editingSession) {
        await remove(`/sessions/${editingSession.id}`);
      } else {
        const item = await request(
          editing === "rename" && editingSession
            ? `/sessions/${editingSession.id}`
            : `/books/${bookId}/sessions`,
          sessionSchema,
          {
            method: editing === "rename" ? "PATCH" : "POST",
            body: { title: title.trim() || "新会话" },
          },
        );
        if (mounted.current && editing === "new") setSessionId(item.id);
      }
      if (mounted.current) {
        setEditing(null);
        await refresh();
      }
    } catch (reason) {
      if (mounted.current) setError(errorText(reason));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <>
      {expanded &&
        sidebarTarget &&
        createPortal(
          <div className="chat-session-list" aria-label="会话列表">
            {sessions.map((session) => (
              <div
                className={`chat-session-row ${session.id === sessionId ? "selected" : ""}`}
                key={session.id}
              >
                <Button
                  variant="ghost"
                  className="chat-session-title"
                  onClick={() => setSessionId(session.id)}
                >
                  {session.title || "未命名会话"}
                </Button>
                <div className="chat-session-actions">
                  <Menu label={`${session.title || "未命名会话"}的操作`} iconOnly>
                    <MenuItem onSelect={() => editSession("rename", session)}>
                      <Pencil size={14} />
                      重命名
                    </MenuItem>
                    <MenuSeparator />
                    <MenuItem danger onSelect={() => editSession("delete", session)}>
                      <Trash2 size={14} />
                      删除会话
                    </MenuItem>
                  </Menu>
                </div>
              </div>
            ))}
            {loaded && sessions.length === 0 && <p className="muted">暂无会话</p>}
          </div>,
          sidebarTarget,
        )}
      <aside
        className={`chat-panel ${expanded ? "chat-panel-expanded" : ""}`}
        aria-label="AI 协作面板"
      >
        <header className="chat-heading">
          {expanded && toolbarStart && (
            <>
              {toolbarStart}
              <span className="toolbar-divider" aria-hidden="true" />
            </>
          )}
          <div className="chat-title-menu">
            {sessions.length ? (
              <Menu label={selected?.title || "AI 协作"} align="start">
                {sessions.map((session) => (
                  <MenuItem key={session.id} onSelect={() => setSessionId(session.id)}>
                    <Check
                      size={14}
                      className={session.id === sessionId ? "opacity-100" : "opacity-0"}
                    />
                    {session.title || "未命名会话"}
                  </MenuItem>
                ))}
                {selected && (
                  <>
                    <MenuSeparator />
                    <MenuItem onSelect={() => editSession("rename", selected)}>
                      <Pencil size={14} />
                      重命名当前会话
                    </MenuItem>
                    <MenuItem danger onSelect={() => editSession("delete", selected)}>
                      <Trash2 size={14} />
                      删除当前会话
                    </MenuItem>
                  </>
                )}
              </Menu>
            ) : (
              <span className="chat-empty-title">AI 协作</span>
            )}
          </div>
          <Button
            variant="ghost"
            aria-label="新建会话"
            title="新建会话"
            onClick={() => {
              setTitle("");
              setEditing("new");
            }}
          >
            <MessageSquarePlus size={18} />
          </Button>
        </header>
        {connection !== "已连接" && connection !== "连接中" && (
          <p className="chat-connection-error" role="status">
            {connection}
          </p>
        )}
        <Alert>{error}</Alert>
        {selected ? (
          <SessionChat
            key={selected.id}
            session={selected}
            models={models}
            defaultModelId={defaultModelId}
            onUpdated={(updated) =>
              setSessions((old) => old.map((item) => (item.id === updated.id ? updated : item)))
            }
            onPending={onPending}
          />
        ) : (
          <EmptyState title={loaded ? "暂无会话" : "正在载入会话…"}>
            {loaded && (
              <Button
                onClick={() => {
                  setTitle("");
                  setEditing("new");
                }}
              >
                新建会话
              </Button>
            )}
          </EmptyState>
        )}
        <Dialog
          title={
            editing === "delete" ? "删除会话" : editing === "rename" ? "重命名会话" : "新建会话"
          }
          open={editing !== null}
          onOpenChange={(open) => {
            if (!open && !busy) setEditing(null);
          }}
        >
          {editing === "delete" ? (
            <p>
              删除「{editingSession?.title || "未命名会话"}
              」及其消息历史？此操作无法撤销。运行或等待回答中的会话须先取消运行。
            </p>
          ) : (
            <Input
              label="会话名称"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              autoFocus
            />
          )}
          <Alert>{error}</Alert>
          <div className="dialog-actions">
            <Button disabled={busy} onClick={() => setEditing(null)}>
              取消
            </Button>
            <Button
              variant={editing === "delete" ? "danger" : "primary"}
              disabled={busy}
              onClick={() => {
                void saveSession();
              }}
            >
              {busy ? "处理中…" : editing === "delete" ? "确认删除" : "保存"}
            </Button>
          </div>
        </Dialog>
      </aside>
    </>
  );
}

export function ChatPanel(props: ChatPanelProps) {
  return <BookChat key={props.bookId} {...props} />;
}
