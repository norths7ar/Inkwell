import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type Ref,
  type ReactNode,
} from "react";
import { z } from "zod";
import { FileText, RotateCcw, Save } from "lucide-react";
import { Alert, Button, Dialog, Input, Textarea } from "../components/ui";
import { errorText } from "../lib/api";

export interface DocumentValue {
  title: string;
  content: string;
}
export interface EditorHandle {
  dirty: boolean;
  save: () => Promise<boolean>;
  stash: () => boolean;
}
const draftSchema = z.object({
  title: z.string(),
  content: z.string(),
  base: z.object({ title: z.string(), content: z.string() }),
});
function equal(a: DocumentValue, b: DocumentValue) {
  return a.title === b.title && a.content === b.content;
}
export function Editor({
  documentKey,
  value,
  kind,
  onSave,
  ref,
  toolbarStart,
  toolbarEnd,
  active = true,
}: {
  documentKey: string;
  value: DocumentValue;
  kind: string;
  onSave: (value: DocumentValue, baseline: DocumentValue) => Promise<void>;
  ref?: Ref<EditorHandle>;
  toolbarStart?: ReactNode;
  toolbarEnd?: ReactNode;
  active?: boolean;
}) {
  const key = `inkwell:draft:${documentKey}`;
  const [initial] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? draftSchema.safeParse(JSON.parse(raw)) : null;
    } catch {
      return null;
    }
  });
  const [form, setForm] = useState<DocumentValue>(
    initial?.success
      ? { title: initial.data.title, content: initial.data.content }
      : { title: value.title, content: value.content },
  );
  const [baseline, setBaseline] = useState(initial?.success ? initial.data.base : value);
  const [savedLocal, setSavedLocal] = useState(Boolean(initial?.success));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reloadOpen, setReloadOpen] = useState(false);
  const dirty = !equal(form, baseline);
  const current = useRef({ form, baseline, dirty });
  useEffect(() => {
    current.current = { form, baseline, dirty };
  }, [form, baseline, dirty]);
  const stash = useCallback(() => {
    try {
      if (dirty) localStorage.setItem(key, JSON.stringify({ ...form, base: baseline }));
      else localStorage.removeItem(key);
      setSavedLocal(dirty);
      return true;
    } catch {
      setError("无法暂存本地草稿，请先保存到作品后再离开。");
      return false;
    }
  }, [key, dirty, form, baseline]);
  useEffect(() => {
    const timer = setTimeout(stash, 500);
    return () => clearTimeout(timer);
  }, [stash]);
  useEffect(() => {
    if (!dirty && !busy && !equal(value, baseline)) {
      setForm(value);
      setBaseline(value);
    }
  }, [value, baseline, dirty, busy]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (current.current.dirty) {
        try {
          localStorage.setItem(
            key,
            JSON.stringify({ ...current.current.form, base: current.current.baseline }),
          );
        } catch {
          /* Browser warns below even if local storage is unavailable. */
        }
        event.preventDefault();
      }
    };
    const pageHide = () => {
      if (current.current.dirty) {
        try {
          localStorage.setItem(
            key,
            JSON.stringify({ ...current.current.form, base: current.current.baseline }),
          );
        } catch {
          /* No UI can be shown during unload. */
        }
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    window.addEventListener("pagehide", pageHide);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      window.removeEventListener("pagehide", pageHide);
    };
  }, [key]);
  const save = useCallback(async () => {
    if (busy) return false;
    if (!dirty) return true;
    setBusy(true);
    setError("");
    try {
      await onSave(
        { title: form.title, content: form.content },
        { title: baseline.title, content: baseline.content },
      );
      localStorage.removeItem(key);
      setBaseline(form);
      setSavedLocal(false);
      return true;
    } catch (error) {
      setError(errorText(error));
      return false;
    } finally {
      setBusy(false);
    }
  }, [busy, dirty, onSave, form, baseline, key]);
  useImperativeHandle(ref, () => ({ dirty, save, stash }), [dirty, save, stash]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (
        active &&
        !document.querySelector('[role="dialog"]') &&
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === "s"
      ) {
        event.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [save, active]);
  const changedElsewhere = dirty && !equal(value, baseline);
  return (
    <section className="editor-panel">
      <div className="panel-toolbar">
        <div className="row editor-info">
          {toolbarStart && (
            <>
              {toolbarStart}
              <span className="toolbar-divider" aria-hidden="true" />
            </>
          )}
          <span className="editor-meta">
            <FileText size={14} />
            <span>{kind}</span>
            <span className="editor-meta-sep" aria-hidden="true">
              ·
            </span>
            <span>
              {[...form.content].filter((char) => !/\s/.test(char)).length.toLocaleString()} 字
            </span>
          </span>
          <span className="editor-meta-sep" aria-hidden="true">
            ·
          </span>
          <span className={`save-status ${dirty ? "is-dirty" : ""}`}>
            <span className="save-dot" aria-hidden="true" />
            <span>{dirty ? (savedLocal ? "未保存（已暂存本地）" : "未保存") : "已保存"}</span>
          </span>
        </div>
        <div className="row editor-actions">
          <Button
            variant="ghost"
            className="icon-control"
            aria-label="重新载入"
            title="重新载入"
            disabled={busy || !dirty}
            onClick={() => setReloadOpen(true)}
          >
            <RotateCcw size={16} />
          </Button>
          <Button
            variant="ghost"
            className={`icon-control editor-save-icon ${dirty ? "is-dirty" : ""}`}
            aria-label="保存"
            title={busy ? "保存中…" : "保存 · Ctrl+S"}
            disabled={busy || !dirty}
            onClick={() => void save()}
          >
            <Save size={16} />
          </Button>
          {toolbarEnd && (
            <>
              <span className="toolbar-divider" aria-hidden="true" />
              {toolbarEnd}
            </>
          )}
        </div>
      </div>
      <Alert>{error}</Alert>
      {initial?.success && dirty && <div className="draft-restored">已恢复本地草稿</div>}
      {changedElsewhere && (
        <div className="notice">
          内容已在别处更新，当前未保存的修改已暂存。如需覆盖，请先复制保留后再重新载入。
        </div>
      )}
      <div className="editor-page">
        <Input
          label="标题"
          value={form.title}
          disabled={busy || kind === "单书规则"}
          onChange={(event) => {
            setForm({ ...form, title: event.target.value });
            setSavedLocal(false);
          }}
        />
        <Textarea
          label={kind === "单书规则" ? "写作规则 · Markdown" : "正文 · Markdown"}
          className="manuscript"
          value={form.content}
          disabled={busy}
          placeholder="正文"
          onChange={(event) => {
            setForm({ ...form, content: event.target.value });
            setSavedLocal(false);
          }}
        />
      </div>
      <div className="editor-footer">
        <span>Markdown · Ctrl + S</span>
      </div>
      <Dialog
        open={reloadOpen}
        onOpenChange={setReloadOpen}
        title="重新载入正文"
        description="这会放弃当前未保存的修改，恢复到上次保存的版本。"
      >
        <div className="row end">
          <Button onClick={() => setReloadOpen(false)}>继续编辑</Button>
          <Button
            variant="danger"
            onClick={() => {
              try {
                localStorage.removeItem(key);
                setForm({ title: value.title, content: value.content });
                setBaseline(value);
                setSavedLocal(false);
                setReloadOpen(false);
              } catch {
                setError("无法清除本地草稿，请检查浏览器存储权限。");
              }
            }}
          >
            放弃草稿并载入
          </Button>
        </div>
      </Dialog>
    </section>
  );
}
