import { useState, type ReactNode } from "react";
import { Check, X } from "lucide-react";
import { diffLines } from "diff";
import { Alert, Badge, Button, EmptyState } from "../components/ui";
import { ApiError, errorText, request } from "../lib/api";
import { pendingSchema, type PendingChange } from "../lib/schema";

function TextDiff({ before, after }: { before: string; after: string }) {
  const parts = diffLines(before, after, { timeout: 50 }) ?? [
    { value: before, removed: true, added: false },
    { value: after, added: true, removed: false },
  ];
  return (
    <div className="diff-grid">
      <div className="diff-before">
        <h4>当前正式内容</h4>
        <pre>
          {before
            ? parts
                .filter((part) => !part.added)
                .map((part, index) => (
                  <span className={part.removed ? "diff-removed" : ""} key={index}>
                    {part.value}
                  </span>
                ))
            : "（空）"}
        </pre>
      </div>
      <div className="diff-after">
        <h4>待采用版本</h4>
        <pre>
          {after
            ? parts
                .filter((part) => !part.removed)
                .map((part, index) => (
                  <span className={part.added ? "diff-added" : ""} key={index}>
                    {part.value}
                  </span>
                ))
            : "（空）"}
        </pre>
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
  changes,
  onChanged,
  beforeApply,
  focusedId,
  showHeading = true,
  toolbarStart,
}: {
  changes: PendingChange[];
  onChanged: () => void;
  beforeApply: (change: PendingChange) => boolean;
  focusedId: string | null;
  showHeading?: boolean;
  toolbarStart?: ReactNode;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
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
    } catch (error) {
      setError(
        error instanceof ApiError && error.status === 409
          ? "正文基线已变化，不能覆盖当前内容。待审项已保留，请对照当前正文重新提出变更，或拒绝此项。"
          : errorText(error),
      );
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
          <Badge>{changes.length} 项待确认</Badge>
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
