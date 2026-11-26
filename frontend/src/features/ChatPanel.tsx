import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  AlertCircle,
  ArrowUp,
  BookOpen,
  Brain,
  Check,
  ChevronRight,
  Globe,
  Lightbulb,
  Loader2,
  Lock,
  Pencil,
  PenLine,
  SearchCheck,
  Sparkles,
  Square,
  SquarePen,
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
import { Markdown } from "../components/Markdown";
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
] as const;

const modeLabels: Readonly<Record<Session["mode"], string>> = {
  discuss: "讨论",
  write: "写作",
  review: "审阅",
};

const questionSchema = z.object({ question: z.string(), options: z.array(z.string()) });

const toolNames: Readonly<Record<string, string>> = {
  list_chapters: "浏览章节目录",
  read_chapter: "读取章节",
  read_summaries: "读取章节摘要",
  read_entries: "查阅设定",
  search_content: "搜索作品",
  propose_entry_edit: "提出设定修改",
  propose_chapter_edit: "提出章节修改",
  ask_user: "确认问题",
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

function toolHint(argumentsJson: string): string {
  if (!argumentsJson.trim()) return "";
  try {
    const raw: unknown = JSON.parse(argumentsJson);
    const parsed = z.record(z.string(), z.unknown()).safeParse(raw);
    if (!parsed.success) return "";
    const record = parsed.data;
    for (const key of ["title", "query", "kind", "question", "category"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) {
        if (key === "kind") {
          return value === "character"
            ? "人物"
            : value === "world"
              ? "世界"
              : value === "note"
                ? "笔记"
                : value;
        }
        return value.trim().slice(0, 28);
      }
    }
  } catch {
    /* Ignore incomplete JSON. */
  }
  return "";
}

function formatTokenCount(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`;
  return String(count);
}

function formatRelativeTime(iso: string): string {
  if (!iso) return "";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (seconds < 60) return "刚刚";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  const months = Math.floor(days / 30);
  if (months < 12) return `大约 ${months} 个月前`;
  return new Date(ts).toLocaleDateString("zh-CN");
}

function parseBlocks(message: Message) {
  try {
    const raw: unknown = JSON.parse(message.blocks_json);
    return z.array(blockSchema).parse(raw);
  } catch {
    return null;
  }
}

type ToolStep = {
  kind: "tool";
  callId: string;
  name: string;
  args: string;
  result: string;
  isError: boolean;
  done: boolean;
};

type ProcessStep =
  | { kind: "reasoning"; id: string; text: string }
  | { kind: "note"; id: string; text: string }
  | ToolStep;

type DisplayTurn =
  | { kind: "user"; id: string; text: string; malformed?: boolean }
  | { kind: "assistant"; id: string; steps: ProcessStep[]; finalText: string; malformed?: boolean };

function groupMessagesIntoTurns(messages: Message[]): DisplayTurn[] {
  const turns: DisplayTurn[] = [];
  let current: {
    id: string;
    segments: {
      id: string;
      hasToolCalls: boolean;
      steps: ProcessStep[];
      texts: string[];
      malformed: boolean;
    }[];
    toolById: Map<string, ToolStep>;
  } | null = null;

  function flushAssistantTurn() {
    if (!current) return;
    const steps: ProcessStep[] = [];
    let finalText = "";
    const segments = current.segments;
    const lastIdx = segments.length - 1;
    const lastSeg = segments[lastIdx];
    const hasFinalNoTool = Boolean(lastSeg && !lastSeg.hasToolCalls && lastSeg.texts.length > 0);
    const hasMalformed = segments.some((seg) => seg.malformed);

    segments.forEach((seg, idx) => {
      for (const step of seg.steps) {
        steps.push(step);
      }
      if (hasFinalNoTool && idx === lastIdx) {
        finalText = seg.texts.join("\n\n");
      } else if (seg.texts.length > 0) {
        steps.push({ kind: "note", id: `${seg.id}:note`, text: seg.texts.join("\n\n") });
      }
    });

    if (!finalText && steps.length > 0) {
      const lastStep = steps.at(-1);
      if (lastStep?.kind === "note") {
        steps.pop();
        finalText = lastStep.text;
      }
    }

    turns.push({
      kind: "assistant",
      id: current.id,
      steps,
      finalText,
      malformed: hasMalformed,
    });
    current = null;
  }

  for (const message of messages) {
    const blocks = parseBlocks(message);
    if (message.role === "user") {
      flushAssistantTurn();
      const userText = blocks
        ? blocks
            .map((block) =>
              block.type === "text"
                ? block.text
                : block.type === "image"
                  ? `[图片 · ${block.media_type}]`
                  : "",
            )
            .filter(Boolean)
            .join("\n")
        : "";
      turns.push({
        kind: "user",
        id: message.id,
        text: userText,
        malformed: blocks === null,
      });
      continue;
    }

    if (!current) {
      current = { id: message.id, segments: [], toolById: new Map() };
    }

    if (!blocks) {
      current.segments.push({
        id: message.id,
        hasToolCalls: false,
        steps: [],
        texts: [],
        malformed: true,
      });
      continue;
    }

    if (message.role === "tool") {
      for (const block of blocks) {
        if (block.type !== "tool_result") continue;
        const existing = current.toolById.get(block.call_id);
        if (existing) {
          existing.result = block.content;
          existing.isError = block.is_error;
          existing.done = true;
        } else {
          const orphan: ToolStep = {
            kind: "tool",
            callId: block.call_id,
            name: block.name,
            args: "",
            result: block.content,
            isError: block.is_error,
            done: true,
          };
          current.toolById.set(block.call_id, orphan);
          const lastSeg = current.segments.at(-1);
          if (lastSeg) {
            lastSeg.steps.push(orphan);
          } else {
            current.segments.push({
              id: message.id,
              hasToolCalls: true,
              steps: [orphan],
              texts: [],
              malformed: false,
            });
          }
        }
      }
      continue;
    }

    const segSteps: ProcessStep[] = [];
    const texts: string[] = [];
    let hasToolCalls = false;
    blocks.forEach((block, index) => {
      if (block.type === "reasoning" && block.text.trim()) {
        segSteps.push({ kind: "reasoning", id: `${message.id}:r:${index}`, text: block.text });
      } else if (block.type === "text" && block.text.trim()) {
        texts.push(block.text);
      } else if (block.type === "tool_call") {
        hasToolCalls = true;
        const step: ToolStep = {
          kind: "tool",
          callId: block.call_id,
          name: block.name,
          args: block.arguments_json,
          result: "",
          isError: false,
          done: false,
        };
        current?.toolById.set(block.call_id, step);
        segSteps.push(step);
      } else if (block.type === "image") {
        texts.push(`[图片 · ${block.media_type}]`);
      }
    });

    current.segments.push({
      id: message.id,
      hasToolCalls,
      steps: segSteps,
      texts,
      malformed: false,
    });
  }

  flushAssistantTurn();
  return turns;
}

function ToolStepRow({ step }: { step: ToolStep }) {
  const [open, setOpen] = useState(false);
  const hint = toolHint(step.args);
  const hasDetails = Boolean(step.args.trim() && step.args.trim() !== "{}") || Boolean(step.result);

  return (
    <div className={`process-tool ${step.isError ? "is-error" : ""}`}>
      <button
        type="button"
        className="process-tool-trigger"
        disabled={!hasDetails}
        aria-expanded={hasDetails ? open : undefined}
        onClick={() => {
          if (hasDetails) setOpen((value) => !value);
        }}
      >
        <span className="process-tool-icon" aria-hidden="true">
          {!step.done ? (
            <Loader2 size={12} className="spin" />
          ) : step.isError ? (
            <AlertCircle size={12} />
          ) : (
            <Check size={12} />
          )}
        </span>
        <span className="process-tool-name">{toolName(step.name)}</span>
        {hint && <span className="process-tool-hint">{hint}</span>}
        {hasDetails && (
          <ChevronRight size={12} className={`process-chevron ${open ? "is-open" : ""}`} />
        )}
      </button>
      {open && hasDetails && (
        <div className="process-tool-body">
          {step.args.trim() && step.args.trim() !== "{}" && (
            <div className="process-tool-detail">{toolSummary(step.args)}</div>
          )}
          {step.result && <div className="process-tool-detail">{toolSummary(step.result)}</div>}
        </div>
      )}
    </div>
  );
}

function ProcessDisclosure({
  steps,
  streaming = false,
  defaultOpen = false,
}: {
  steps: ProcessStep[];
  streaming?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const wasStreamingRef = useRef(streaming);
  const userToggledRef = useRef(false);

  useEffect(() => {
    if (streaming) {
      wasStreamingRef.current = true;
      if (!userToggledRef.current) setOpen(true);
      return;
    }
    if (wasStreamingRef.current) {
      wasStreamingRef.current = false;
      if (!userToggledRef.current) setOpen(false);
    }
  }, [streaming]);

  if (steps.length === 0) return null;

  const hasReasoning = steps.some((step) => step.kind === "reasoning");
  const tools = steps.filter((step): step is ToolStep => step.kind === "tool");
  const runningTool = tools.find((tool) => !tool.done);

  let label = "思考过程";
  if (streaming) {
    if (runningTool) {
      label = `正在${toolName(runningTool.name)}…`;
    } else if (hasReasoning) {
      label = "正在思考…";
    } else {
      label = "正在整理…";
    }
  } else if (hasReasoning && tools.length > 0) {
    label = `已思考 · 调用 ${tools.length} 项工具`;
  } else if (tools.length > 0) {
    label = `已调用 ${tools.length} 项工具`;
  }

  return (
    <div className={`process-block ${streaming ? "is-streaming" : ""}`}>
      <button
        type="button"
        className="process-toggle"
        aria-expanded={open}
        onClick={() => {
          userToggledRef.current = true;
          setOpen((value) => !value);
        }}
      >
        <Brain size={13} className="process-toggle-icon" />
        <span>{label}</span>
        <ChevronRight size={13} className={`process-chevron ${open ? "is-open" : ""}`} />
      </button>
      {open && (
        <div className="process-timeline">
          {steps.map((step) => {
            if (step.kind === "reasoning") {
              return (
                <div className="process-reasoning" key={step.id}>
                  <Markdown content={step.text} />
                </div>
              );
            }
            if (step.kind === "note") {
              return (
                <div className="process-note" key={step.id}>
                  <Markdown content={step.text} />
                </div>
              );
            }
            return <ToolStepRow key={step.callId} step={step} />;
          })}
        </div>
      )}
    </div>
  );
}

function TurnView({ turn }: { turn: DisplayTurn }) {
  if (turn.kind === "user") {
    return (
      <article className="message message-user">
        {turn.malformed ? (
          <Alert>这条历史消息格式无法识别。</Alert>
        ) : (
          <div className="message-user-text">{turn.text}</div>
        )}
      </article>
    );
  }

  return (
    <article className="message message-assistant">
      {turn.malformed && <Alert>这条历史消息格式无法识别。</Alert>}
      <ProcessDisclosure steps={turn.steps} />
      {turn.finalText && <Markdown content={turn.finalText} className="message-markdown" />}
    </article>
  );
}

type ToolState = { id: string; name: string; status: "running" | "done" | "error" };
type LiveTurn = { runId: string; text: string; reasoning: string; tools: ToolState[] };
type LiveQuestion = { runId: string; question: string; options: string[] };
const emptyTurn = (): LiveTurn => ({ runId: "", text: "", reasoning: "", tools: [] });

function ComposerArea({
  text,
  onTextChange,
  mode,
  onModeChange,
  modelId,
  onModelChange,
  models,
  visibilityScope,
  onVisibilityChange,
  visibilityPermanent,
  running,
  waiting,
  locked,
  busy,
  availableModel,
  inputRef,
  onSend,
  onSteer,
  runStatusText,
  canCancel,
  onCancel,
}: {
  text: string;
  onTextChange: (value: string) => void;
  mode: Session["mode"];
  onModeChange: (value: Session["mode"]) => void;
  modelId: string;
  onModelChange: (value: string) => void;
  models: Model[];
  visibilityScope: Session["visibility_scope"];
  onVisibilityChange: (next: Session["visibility_scope"]) => void;
  visibilityPermanent: boolean;
  running: boolean;
  waiting: boolean;
  locked: boolean;
  busy: boolean;
  availableModel: boolean;
  inputRef?: React.RefObject<HTMLTextAreaElement | null>;
  onSend: () => void;
  onSteer?: () => void;
  runStatusText?: string;
  canCancel?: boolean;
  onCancel?: () => void;
}) {
  return (
    <div className="chat-composer-area">
      {(waiting || running) && runStatusText && (
        <div className="chat-run-status">
          <span>{runStatusText}</span>
          {canCancel && onCancel && (
            <Button variant="ghost" disabled={busy} onClick={onCancel}>
              <Square size={13} />
              停止生成
            </Button>
          )}
        </div>
      )}
      <div className="chat-composer">
        <Textarea
          ref={inputRef}
          aria-label={running ? "中途补充要求" : "输入消息"}
          placeholder={running ? "补充一句…" : "输入想法或写作要求…"}
          value={text}
          onChange={(event) => onTextChange(event.target.value)}
          disabled={waiting}
          rows={3}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !running) {
              event.preventDefault();
              onSend();
            }
          }}
        />
        <div className="composer-footer">
          <div className="composer-options">
            <Select
              compact
              label="创作模式"
              value={mode}
              disabled={locked}
              icon={
                mode === "discuss" ? (
                  <Lightbulb size={12} />
                ) : mode === "write" ? (
                  <PenLine size={12} />
                ) : (
                  <SearchCheck size={12} />
                )
              }
              options={[...modes]}
              onChange={(value) => onModeChange(modeSchema.parse(value))}
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
              onChange={onModelChange}
            />
          </div>
          <div className="composer-actions">
            {visibilityPermanent && visibilityScope === "global" ? (
              <button
                type="button"
                className="composer-visibility-pill is-global"
                disabled
                title="已开启全局资料，本对话中不可退回仅公开"
              >
                <Globe size={12} />
                <span>全局</span>
                <Lock size={10} />
              </button>
            ) : (
              <button
                type="button"
                className={`composer-visibility-pill ${visibilityScope === "global" ? "is-global" : ""}`}
                disabled={locked}
                title={
                  visibilityScope === "global"
                    ? "点击切回仅公开资料"
                    : "当前仅读取公开资料；点击开启全局资料（开启后本对话不可退回）"
                }
                onClick={() =>
                  onVisibilityChange(visibilityScope === "global" ? "local" : "global")
                }
              >
                {visibilityScope === "global" ? <Globe size={12} /> : <BookOpen size={12} />}
                <span>{visibilityScope === "global" ? "全局" : "仅公开"}</span>
              </button>
            )}
            {running && onSteer ? (
              <Button
                variant="primary"
                className="composer-send"
                aria-label="插话"
                title="插话"
                disabled={busy || !text.trim()}
                onClick={onSteer}
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
                onClick={onSend}
              >
                <ArrowUp size={16} />
              </Button>
            )}
          </div>
        </div>
      </div>
      {!availableModel && !waiting && <p className="chat-model-warning">尚未配置可用模型</p>}
    </div>
  );
}

function SessionChat({
  session,
  models,
  defaultModelId,
  modelId,
  onModelChange,
  initialRuns,
  onRunsChange,
  onUpdated,
  onPending,
  inputRef,
}: {
  session: Session;
  models: Model[];
  defaultModelId: string | null;
  modelId: string;
  onModelChange: (value: string) => void;
  initialRuns?: Run[];
  onRunsChange: (runs: Run[]) => void;
  onUpdated: (session: Session) => void;
  onPending: (id: string) => void;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<Run[]>(initialRuns ?? []);
  const [live, setLive] = useState<LiveTurn>(emptyTurn);
  const [text, setText] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
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
  const onRunsChangeRef = useRef(onRunsChange);
  onRunsChangeRef.current = onRunsChange;

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
      onRunsChangeRef.current(records);
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
        if (event.resource !== "session_deleted" && event.session_id === session.id) void refresh();
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
          { id: event.change.id, title: event.change.title || "待审建议" },
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
          if (event.error) setNotice(event.error.message);
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
      /* Malformed question falls back to generic prompt. */
    }
  }
  const effectiveModel = modelId === "default" ? defaultModelId : modelId;
  const availableModel = models.some((model) => model.id === effectiveModel && model.enabled);

  const turns = useMemo(() => groupMessagesIntoTurns(messages), [messages]);
  const liveSteps: ProcessStep[] = [];
  if (live.reasoning) {
    liveSteps.push({ kind: "reasoning", id: "live-reasoning", text: live.reasoning });
  }
  for (const tool of live.tools) {
    if (tool.status === "running") {
      liveSteps.push({
        kind: "tool",
        callId: tool.id,
        name: tool.name,
        args: "",
        result: "",
        isError: false,
        done: false,
      });
    }
  }
  const showLiveStream = Boolean(live.text) || liveSteps.length > 0;

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
      setRuns((old) => {
        const next = [run, ...old.filter((item) => item.id !== run.id)];
        onRunsChangeRef.current(next);
        return next;
      });
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
      setRuns((old) => {
        const next = [run, ...old.filter((item) => item.id !== waiting && item.id !== run.id)];
        onRunsChangeRef.current(next);
        return next;
      });
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
          重新载入对话
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
          <p className="muted">正在载入对话…</p>
        ) : (
          turns.length === 0 && !showLiveStream && <EmptyState title="暂无消息" />
        )}
        {turns.map((turn) => (
          <TurnView key={turn.id} turn={turn} />
        ))}
        {showLiveStream && (
          <article className="message message-assistant stream" aria-label="正在生成">
            <ProcessDisclosure
              steps={liveSteps}
              streaming={running && !live.text}
              defaultOpen={!live.text}
            />
            {live.text && <Markdown content={live.text} className="message-markdown" />}
          </article>
        )}
        {pending.length > 0 && (
          <div className="stack">
            <span className="section-title">待审建议</span>
            {pending.map((item) => (
              <Button key={item.id} onClick={() => onPending(item.id)}>
                {item.title} · 去审阅
              </Button>
            ))}
          </div>
        )}
        {notice && !(latest?.status === "failed" && latest.error) && (
          <p className="chat-notice" role="status">
            {notice}
          </p>
        )}
        {latest?.status === "failed" && latest.error && <Alert>{latest.error.message}</Alert>}
        {waiting && (
          <section className="question-card stack" aria-label="等待你的决定">
            <Badge tone="warning">需要你的决定</Badge>
            <Markdown content={question?.question || "请选择或输入你的想法以继续。"} />
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
              label="你的想法"
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
              继续对话
            </Button>
          </section>
        )}
      </div>
      <ComposerArea
        text={text}
        onTextChange={setText}
        mode={session.mode}
        onModeChange={(nextMode) => {
          void perform(async () =>
            onUpdated(
              await request(`/sessions/${session.id}`, sessionSchema, {
                method: "PATCH",
                body: { mode: nextMode },
              }),
            ),
          );
        }}
        modelId={modelId}
        onModelChange={onModelChange}
        models={models}
        visibilityScope={session.visibility_scope}
        onVisibilityChange={(nextScope) => {
          if (nextScope !== "global" || session.visibility_scope === "global") return;
          void perform(async () =>
            onUpdated(
              await request(`/sessions/${session.id}`, sessionSchema, {
                method: "PATCH",
                body: { visibility_scope: "global" },
              }),
            ),
          );
        }}
        visibilityPermanent
        running={running}
        waiting={Boolean(waiting)}
        locked={locked}
        busy={busy}
        availableModel={availableModel}
        inputRef={inputRef}
        onSend={() => void send()}
        onSteer={() => {
          const id = active?.id || live.runId;
          if (!id) return;
          const sent = text;
          void perform(async () => {
            await request(
              `/runs/${id}/steer`,
              z.object({ run_id: z.string(), status: z.string() }),
              {
                method: "POST",
                body: { text: sent },
              },
            );
            setText((current) => (current === sent ? "" : current));
          });
        }}
        runStatusText={waiting ? "等待你的决定" : running ? "正在生成…" : undefined}
        canCancel={Boolean(active || eventRunning || waiting)}
        onCancel={() => {
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
      />
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
  const [drafting, setDrafting] = useState(false);
  const [draftText, setDraftText] = useState("");
  const [draftMode, setDraftMode] = useState<Session["mode"]>("discuss");
  const [draftModelId, setDraftModelId] = useState("default");
  const [draftVisibility, setDraftVisibility] = useState<Session["visibility_scope"]>("local");
  const [runsBySession, setRunsBySession] = useState<Record<string, Run[]>>({});
  const [models, setModels] = useState<Model[]>([]);
  const [defaultModelId, setDefaultModelId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<"rename" | "delete" | null>(null);
  const [editingId, setEditingId] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const sequence = useRef(0);
  const mounted = useRef(true);
  const draftingRef = useRef(false);
  draftingRef.current = drafting;
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null);
  const connection = useConnection();

  const focusComposer = useCallback(() => {
    window.setTimeout(() => composerInputRef.current?.focus(), 0);
  }, []);

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
      setSessionId((old) => {
        if (draftingRef.current) return "";
        return items.some((item) => item.id === old) ? old : items[0]?.id || "";
      });
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
  const selectedRuns = selected ? runsBySession[selected.id] : undefined;

  function openNewBlankChat() {
    setError("");
    setDrafting(true);
    setSessionId("");
    focusComposer();
  }

  function selectExistingSession(id: string) {
    setDrafting(false);
    setSessionId(id);
  }

  function editSession(action: "rename" | "delete", session: Session) {
    setEditingId(session.id);
    setTitle(session.title);
    setEditing(action);
  }

  async function saveSessionAction() {
    if (!editing || !editingSession) {
      setEditing(null);
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (editing === "delete") {
        const removedId = editingSession.id;
        await remove(`/sessions/${removedId}`);
        setSessions((old) => old.filter((item) => item.id !== removedId));
        if (sessionId === removedId) {
          setSessionId("");
        }
      } else {
        await request(`/sessions/${editingSession.id}`, sessionSchema, {
          method: "PATCH",
          body: { title: title.trim() || "未命名对话" },
        });
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

  async function sendBlankDraft() {
    if (!draftText.trim() || busy) return;
    const sent = draftText;
    setBusy(true);
    setError("");
    let createdSession: Session | null = null;
    try {
      createdSession = await request(`/books/${bookId}/sessions`, sessionSchema, {
        method: "POST",
        body: {
          title: "",
          mode: draftMode,
          visibility_scope: draftVisibility,
        },
      });
      const run = await request(`/sessions/${createdSession.id}/runs`, runSchema, {
        method: "POST",
        body: { text: sent, model_id: draftModelId === "default" ? null : draftModelId },
      });
      if (!mounted.current) return;
      setDraftText("");
      setDrafting(false);
      setRunsBySession((old) => ({ ...old, [createdSession!.id]: [run] }));
      setSessions((old) => [createdSession!, ...old]);
      setSessionId(createdSession.id);
      await refresh();
    } catch (reason) {
      if (mounted.current) {
        if (createdSession) {
          setDrafting(false);
          setSessionId(createdSession.id);
          await refresh();
        }
        setError(errorText(reason));
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  const effectiveDraftModel = draftModelId === "default" ? defaultModelId : draftModelId;
  const availableDraftModel = models.some(
    (model) => model.id === effectiveDraftModel && model.enabled,
  );

  let usageRow: ReactNode = null;
  if (selected && selectedRuns && selectedRuns.length > 0) {
    const totals = selectedRuns.reduce(
      (acc, run) => ({
        input: acc.input + run.input_tokens,
        output: acc.output + run.output_tokens,
        cache: acc.cache + run.cache_read_tokens,
      }),
      { input: 0, output: 0, cache: 0 },
    );
    const latestWithTokens = selectedRuns.find((run) => run.input_tokens > 0);
    const latestModel = latestWithTokens?.model_id
      ? models.find((item) => item.id === latestWithTokens.model_id)
      : models.find((item) => item.id === defaultModelId);
    const contextLimit = latestModel?.context_window ?? null;
    const latestInput = latestWithTokens?.input_tokens ?? 0;
    if (totals.input > 0 || totals.output > 0) {
      const pct =
        contextLimit && contextLimit > 0
          ? Math.min(100, (latestInput / contextLimit) * 100).toFixed(1)
          : null;
      usageRow = (
        <div className="chat-header-usage">
          <span>
            输入 {formatTokenCount(totals.input)} · 输出 {formatTokenCount(totals.output)}
            {totals.cache > 0 && ` · 缓存 ${formatTokenCount(totals.cache)}`}
          </span>
          {latestInput > 0 && (
            <span>
              上下文 {formatTokenCount(latestInput)}
              {contextLimit ? ` / ${formatTokenCount(contextLimit)} (${pct}%)` : ""}
            </span>
          )}
        </div>
      );
    }
  }

  return (
    <>
      {expanded &&
        sidebarTarget &&
        createPortal(
          <>
            <div className="directory-section-heading">
              <h2>对话</h2>
              <div className="directory-section-actions">
                <span className="directory-meta">{sessions.length}</span>
                <Button
                  variant="ghost"
                  className="icon-control"
                  aria-label="新对话"
                  title="新对话"
                  onClick={openNewBlankChat}
                >
                  <SquarePen size={14} />
                </Button>
              </div>
            </div>
            <div className="chat-session-list" aria-label="对话列表">
              {sessions.map((session) => (
                <div
                  className={`chat-session-row ${session.id === sessionId ? "selected" : ""}`}
                  key={session.id}
                >
                  <button
                    type="button"
                    className="chat-session-button"
                    onClick={() => selectExistingSession(session.id)}
                  >
                    <span className="chat-session-title">{session.title || "新对话"}</span>
                    <span className="chat-session-meta">
                      <span>
                        {formatRelativeTime(session.updated_at || session.created_at) || "刚才"}
                      </span>
                      <span>·</span>
                      <span>{modeLabels[session.mode]}</span>
                      {session.visibility_scope === "global" && <Globe size={11} />}
                    </span>
                  </button>
                  <div className="chat-session-actions">
                    <Menu label={`${session.title || "新对话"}的操作`} iconOnly>
                      <MenuItem onSelect={() => editSession("rename", session)}>
                        <Pencil size={14} />
                        重命名
                      </MenuItem>
                      <MenuSeparator />
                      <MenuItem danger onSelect={() => editSession("delete", session)}>
                        <Trash2 size={14} />
                        删除
                      </MenuItem>
                    </Menu>
                  </div>
                </div>
              ))}
              {loaded && sessions.length === 0 && <p className="directory-empty">暂无对话</p>}
            </div>
          </>,
          sidebarTarget,
        )}
      <aside
        className={`chat-panel ${expanded ? "chat-panel-expanded" : ""}`}
        aria-label="AI 协作面板"
      >
        <header className="chat-header">
          <div className="chat-heading">
            {expanded && toolbarStart && (
              <>
                {toolbarStart}
                <span className="toolbar-divider" aria-hidden="true" />
              </>
            )}
            <div className="chat-title-menu">
              {sessions.length > 0 ? (
                <Menu label={selected ? selected.title || "新对话" : "新对话"} align="start">
                  {sessions.map((session) => (
                    <MenuItem key={session.id} onSelect={() => selectExistingSession(session.id)}>
                      <Check
                        size={14}
                        className={session.id === sessionId ? "opacity-100" : "opacity-0"}
                      />
                      {session.title || "新对话"}
                    </MenuItem>
                  ))}
                  {selected && (
                    <>
                      <MenuSeparator />
                      <MenuItem onSelect={() => editSession("rename", selected)}>
                        <Pencil size={14} />
                        重命名对话
                      </MenuItem>
                      <MenuItem danger onSelect={() => editSession("delete", selected)}>
                        <Trash2 size={14} />
                        删除对话
                      </MenuItem>
                    </>
                  )}
                </Menu>
              ) : (
                <span className="chat-empty-title">新对话</span>
              )}
            </div>
            <Button variant="ghost" aria-label="新对话" title="新对话" onClick={openNewBlankChat}>
              <SquarePen size={16} />
            </Button>
          </div>
          {usageRow}
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
            modelId={draftModelId}
            onModelChange={setDraftModelId}
            initialRuns={runsBySession[selected.id]}
            onRunsChange={(records) =>
              setRunsBySession((old) => ({ ...old, [selected.id]: records }))
            }
            onUpdated={(updated) =>
              setSessions((old) => old.map((item) => (item.id === updated.id ? updated : item)))
            }
            onPending={onPending}
            inputRef={composerInputRef}
          />
        ) : (
          <>
            <div className="chat-history" aria-label="空白对话">
              {!loaded ? (
                <EmptyState title="正在载入对话…" />
              ) : (
                <EmptyState title="开始一段新对话">
                  <p className="muted">输入想法即可直接开始，对话标题会自动生成。</p>
                </EmptyState>
              )}
            </div>
            <ComposerArea
              text={draftText}
              onTextChange={setDraftText}
              mode={draftMode}
              onModeChange={setDraftMode}
              modelId={draftModelId}
              onModelChange={setDraftModelId}
              models={models}
              visibilityScope={draftVisibility}
              onVisibilityChange={setDraftVisibility}
              visibilityPermanent={false}
              running={false}
              waiting={false}
              locked={busy || !loaded}
              busy={busy}
              availableModel={availableDraftModel}
              inputRef={composerInputRef}
              onSend={() => void sendBlankDraft()}
            />
          </>
        )}
        <Dialog
          title={editing === "delete" ? "删除对话" : "重命名对话"}
          open={editing !== null}
          onOpenChange={(open) => {
            if (!open && !busy) setEditing(null);
          }}
        >
          {editing === "delete" ? (
            <p>确定删除「{editingSession?.title || "新对话"}」吗？删除后无法恢复。</p>
          ) : (
            <Input
              label="对话标题"
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
                void saveSessionAction();
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
