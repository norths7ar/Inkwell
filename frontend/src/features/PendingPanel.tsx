import { useState, type ReactNode } from "react";
import { Check, CheckCheck, X, XCircle } from "lucide-react";
import { diffChars, diffLines, type Change } from "diff";
import { Alert, Badge, Button, EmptyState } from "../components/ui";
import { ApiError, errorText, request } from "../lib/api";
import { batchChangesResultSchema, pendingSchema, type PendingChange } from "../lib/schema";

function splitLinesPreserve(value: string): string[] {
  const lines = value.split(/(?<=\n)/);
  return lines.filter((line) => line.length > 0);
}

function renderInlineChars(
  oldLine: string,
  newLine: string,
  side: "before" | "after",
  key: string,
) {
  const charParts = diffChars(oldLine, newLine, { timeout: 50 }) ?? [];
  return (
    <span
      key={key}
      className={side === "before" ? "diff-line-changed-old" : "diff-line-changed-new"}
    >
      {charParts
        .filter((part) => (side === "before" ? !part.added : !part.removed))
        .map((part, idx) => (
          <span
            key={idx}
            className={
              side === "before"
                ? part.removed
                  ? "diff-removed"
                  : ""
                : part.added
                  ? "diff-added"
                  : ""
            }
          >
            {part.value}
          </span>
        ))}
    </span>
  );
}

function renderPairedSides(parts: Change[]) {
  const beforeNodes: ReactNode[] = [];
  const afterNodes: ReactNode[] = [];

  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i];
    if (!part) continue;
    const next = parts[i + 1];

    if (part.removed && next?.added) {
      const oldLines = splitLinesPreserve(part.value);
      const newLines = splitLinesPreserve(next.value);
      const paired = Math.min(oldLines.length, newLines.length);
      for (let j = 0; j < paired; j += 1) {
        const oldLine = oldLines[j] ?? "";
        const newLine = newLines[j] ?? "";
        if (Math.max(oldLine.trim().length, newLine.trim().length) >= 12) {
          beforeNodes.push(renderInlineChars(oldLine, newLine, "before", `b-${i}-${j}`));
          afterNodes.push(renderInlineChars(oldLine, newLine, "after", `a-${i}-${j}`));
        } else {
          beforeNodes.push(
            <span key={`b-${i}-${j}`} className="diff-removed">
              {oldLine}
            </span>,
          );
          afterNodes.push(
            <span key={`a-${i}-${j}`} className="diff-added">
              {newLine}
            </span>,
          );
        }
      }
      for (let j = paired; j < oldLines.length; j += 1) {
        beforeNodes.push(
          <span key={`b-rem-${i}-${j}`} className="diff-removed">
            {oldLines[j]}
          </span>,
        );
      }
      for (let j = paired; j < newLines.length; j += 1) {
        afterNodes.push(
          <span key={`a-add-${i}-${j}`} className="diff-added">
            {newLines[j]}
          </span>,
        );
      }
      i += 1;
      continue;
    }

    if (part.removed) {
      beforeNodes.push(
        <span key={`b-${i}`} className="diff-removed">
          {part.value}
        </span>,
      );
    } else if (part.added) {
      afterNodes.push(
        <span key={`a-${i}`} className="diff-added">
          {part.value}
        </span>,
      );
    } else {
      beforeNodes.push(<span key={`b-eq-${i}`}>{part.value}</span>);
      afterNodes.push(<span key={`a-eq-${i}`}>{part.value}</span>);
    }
  }

  return { beforeNodes, afterNodes };
}

function TextDiff({ before, after }: { before: string; after: string }) {
  const parts: Change[] = diffLines(before, after, { timeout: 50 }) ?? [
    { value: before, removed: true, added: false, count: 1 },
    { value: after, added: true, removed: false, count: 1 },
  ];
  const { beforeNodes, afterNodes } = renderPairedSides(parts);
  return (
    <div className="diff-grid">
      <div className="diff-before">
        <h4>当前正式内容</h4>
        <pre>{before ? beforeNodes : "（空）"}</pre>
      </div>
      <div className="diff-after">
        <h4>待采用版本</h4>
        <pre>{after ? afterNodes : "（空）"}</pre>
      </div>
    </div>
  );
}

function actionLabel(action: PendingChange["action"]) {
  return action === "create" ? "新增" : action === "delete" ? "删除" : "修改";
}

function targetLabel(targetType: PendingChange["target_type"]) {
  return targetType === "chapter" ? "章节" : "设定";
}

export function PendingPanel({
  bookId,
  changes,
  totalCount,
  onChanged,
  beforeApply,
  beforeApplyAll,
  focusedId,
  showHeading = true,
  toolbarStart,
}: {
  bookId?: string;
  changes: PendingChange[];
  totalCount?: number;
  onChanged: () => void;
  beforeApply: (change: PendingChange) => boolean;
  beforeApplyAll?: () => boolean;
  focusedId: string | null;
  showHeading?: boolean;
  toolbarStart?: ReactNode;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const badgeCount = totalCount ?? changes.length;

  async function resolve(change: PendingChange, action: "apply" | "reject") {
    if (action === "apply" && !beforeApply(change)) {
      setError("请先保存或处理编辑区的未保存草稿，再应用此变更。");
      return;
    }
    setBusy(change.id);
    setError("");
    try {
      await request(`/pending-changes/${change.id}/${action}`, pendingSchema, { method: "POST" });
      onChanged();
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 409
          ? "正文基线已变化，不能覆盖当前内容。待审项已保留，请对照当前正文重新提出变更，或拒绝此项。"
          : errorText(failure),
      );
    } finally {
      setBusy(null);
    }
  }

  async function resolveAll(action: "apply" | "reject") {
    if (!bookId) return;
    if (action === "apply" && beforeApplyAll && !beforeApplyAll()) {
      setError("请先保存或处理编辑区的未保存草稿，再批量应用变更。");
      return;
    }
    setBusy(`batch:${action}`);
    setError("");
    try {
      await request(`/books/${bookId}/pending-changes/${action}`, batchChangesResultSchema, {
        method: "POST",
        body: {},
      });
      onChanged();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="pending-panel" aria-label="待审变更">
      {showHeading && (
        <div className="panel-toolbar">
          <div className="row">
            {toolbarStart}
            <h2>待审变更</h2>
          </div>
          <div className="row">
            {bookId && badgeCount > 1 && (
              <>
                <Button disabled={busy !== null} onClick={() => void resolveAll("reject")}>
                  <XCircle size={14} />
                  全部拒绝
                </Button>
                <Button
                  variant="primary"
                  disabled={busy !== null}
                  onClick={() => void resolveAll("apply")}
                >
                  <CheckCheck size={14} />
                  全部应用
                </Button>
              </>
            )}
            <Badge>{badgeCount} 项待确认</Badge>
          </div>
        </div>
      )}
      {!showHeading && changes.length === 0 && toolbarStart && (
        <div className="pending-review-header">
          <div className="pending-review-title">
            {toolbarStart}
            <span className="toolbar-divider" aria-hidden="true" />
            <h3>待审变更</h3>
          </div>
        </div>
      )}
      {!showHeading && bookId && badgeCount > 1 && (
        <div className="pending-batch-strip">
          <span className="muted">共 {badgeCount} 项待审变更</span>
          <div className="row">
            <Button disabled={busy !== null} onClick={() => void resolveAll("reject")}>
              <XCircle size={14} />
              全部拒绝
            </Button>
            <Button
              variant="primary"
              disabled={busy !== null}
              onClick={() => void resolveAll("apply")}
            >
              <CheckCheck size={14} />
              全部应用
            </Button>
          </div>
        </div>
      )}
      <Alert>{error}</Alert>
      {changes.length === 0 ? (
        <EmptyState title="暂无待审变更" />
      ) : (
        changes.map((change, index) => (
          <article
            key={change.id}
            id={`pending-${change.id}`}
            className={`pending-review ${focusedId === change.id ? "focused" : ""}`}
          >
            <div className="pending-review-header">
              <div className="pending-review-title">
                {!showHeading && index === 0 && toolbarStart && (
                  <>
                    {toolbarStart}
                    <span className="toolbar-divider" aria-hidden="true" />
                  </>
                )}
                <h3>
                  {actionLabel(change.action)}
                  {targetLabel(change.target_type)}：{change.title || "正文变更"}
                </h3>
              </div>
              <div className="pending-review-actions">
                <Button
                  variant="primary"
                  className="pending-apply-button"
                  disabled={busy !== null}
                  onClick={() => void resolve(change, "apply")}
                >
                  <Check size={14} />
                  {busy === change.id ? "处理中" : "应用变更"}
                </Button>
                <Button
                  className="pending-reject-button"
                  disabled={busy !== null}
                  onClick={() => void resolve(change, "reject")}
                >
                  <X size={14} />
                  拒绝
                </Button>
              </div>
            </div>
            <div className="pending-meta-strip">
              <span className="pending-meta-item">
                <span>目标类型</span>
                <strong>{targetLabel(change.target_type)}</strong>
              </span>
              <span className="pending-meta-item">
                <span>操作</span>
                <strong>{actionLabel(change.action)}</strong>
              </span>
              {change.created_at && (
                <span className="pending-meta-item">
                  <span>创建时间</span>
                  <strong>{new Date(change.created_at).toLocaleString("zh-CN")}</strong>
                </span>
              )}
            </div>
            <div className="pending-review-body">
              <div className="pending-legend-row">
                <span className="pending-legend-label">正文对照</span>
                <div className="pending-legend-pills">
                  <span className="legend-pill legend-pill-added">新增</span>
                  <span className="legend-pill legend-pill-updated">修改</span>
                  <span className="legend-pill legend-pill-removed">删除</span>
                </div>
              </div>
              <TextDiff before={change.before_content} after={change.after_content} />
            </div>
          </article>
        ))
      )}
    </section>
  );
}
