import { useEffect, useState } from "react";
import { z } from "zod";
import { RotateCcw } from "lucide-react";
import { diffLines } from "diff";
import { Alert, Badge, Button, Dialog, EmptyState } from "../components/ui";
import { errorText, request } from "../lib/api";
import { restoreResultSchema, revisionSchema, type Revision } from "../lib/schema";

const sourceLabels: Readonly<Record<Revision["source"], string>> = {
  user: "手动保存",
  pending_apply: "采用建议",
  restore: "版本恢复",
};

function RevisionPreview({
  revision,
  currentContent,
}: {
  revision: Revision;
  currentContent: string;
}) {
  const parts = diffLines(currentContent, revision.content, { timeout: 50 }) ?? [];
  return (
    <div className="revision-diff-box">
      <div className="revision-diff-meta">
        <strong>{revision.title || "无标题"}</strong>
        <span className="muted">
          {new Date(revision.created_at).toLocaleString("zh-CN")} ·{" "}
          {[...revision.content].filter((c) => !/\s/.test(c)).length} 字
        </span>
      </div>
      <pre className="revision-diff-pre">
        {parts.length === 0
          ? revision.content || "（空白）"
          : parts.map((part, idx) => (
              <span
                key={idx}
                className={part.added ? "diff-added" : part.removed ? "diff-removed" : ""}
              >
                {part.value}
              </span>
            ))}
      </pre>
    </div>
  );
}

export function RevisionDialog({
  open,
  onOpenChange,
  bookId,
  targetType,
  targetId,
  currentTitle,
  currentContent,
  onRestored,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bookId: string;
  targetType: "chapter" | "entry";
  targetId: string;
  currentTitle: string;
  currentContent: string;
  onRestored: () => void;
}) {
  const [items, setItems] = useState<Revision[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setError("");
    void request(
      `/books/${bookId}/revisions?target_type=${targetType}&target_id=${targetId}`,
      z.array(revisionSchema),
    )
      .then((list) => {
        if (!active) return;
        setItems(list);
        setSelectedId(list[0]?.id ?? "");
      })
      .catch((failure: unknown) => {
        if (active) setError(errorText(failure));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open, bookId, targetType, targetId]);

  const selected = items.find((item) => item.id === selectedId) ?? items[0] ?? null;

  async function restore() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const prefix = targetType === "chapter" ? "chapters" : "entries";
      await request(`/${prefix}/${targetId}/restore`, restoreResultSchema, {
        method: "POST",
        body: { revision_id: selected.id },
      });
      onRestored();
      onOpenChange(false);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onOpenChange(false);
      }}
      title={`修订历史 · ${currentTitle || "当前文档"}`}
      wide
      className="revision-dialog"
    >
      <Alert>{error}</Alert>
      {loading ? (
        <p className="muted">正在读取修订历史…</p>
      ) : items.length === 0 ? (
        <EmptyState title="暂无修订历史" />
      ) : (
        <div className="revision-split">
          <div className="revision-list" role="listbox" aria-label="修订列表">
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`revision-item ${selected?.id === item.id ? "is-selected" : ""}`}
                onClick={() => setSelectedId(item.id)}
              >
                <div className="revision-item-top">
                  <Badge>{sourceLabels[item.source]}</Badge>
                  <span>{new Date(item.created_at).toLocaleString("zh-CN")}</span>
                </div>
                <strong className="revision-item-title">{item.title || "无标题"}</strong>
              </button>
            ))}
          </div>
          <div className="revision-detail">
            {selected && (
              <>
                <RevisionPreview revision={selected} currentContent={currentContent} />
                <div className="dialog-actions">
                  <Button disabled={busy} onClick={() => onOpenChange(false)}>
                    关闭
                  </Button>
                  <Button variant="primary" disabled={busy} onClick={() => void restore()}>
                    <RotateCcw size={14} />
                    {busy ? "恢复中…" : "恢复到此版本"}
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}
