import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Eye,
  FilePlus2,
  Folder,
  FolderInput,
  FolderPlus,
  GripVertical,
  LibraryBig,
  ListChecks,
  MessagesSquare,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Plus,
  Search,
  Settings2,
  Sparkles,
  Trash2,
} from "lucide-react";
import {
  Alert,
  Button,
  Dialog,
  EmptyState,
  Input,
  Menu,
  MenuItem,
  MenuSeparator,
  MenuSub,
  Select,
} from "../components/ui";
import { ApiError, errorText, remove, request } from "../lib/api";
import { useEvents } from "../lib/events";
import {
  chapterSchema,
  entrySchema,
  pendingSchema,
  searchSchema,
  volumeSchema,
  type Book,
  type Chapter,
  type Entry,
  type Volume,
} from "../lib/schema";
import { Editor, type DocumentValue, type EditorHandle } from "./Editor";
import { PendingPanel } from "./PendingPanel";
import { ChatPanel } from "./ChatPanel";
import "./workspace.css";

export type AppRoute = "shelf" | "chapters" | "entries" | "discussion" | "pending";
type BookRoute = Exclude<AppRoute, "shelf">;
type Selection = { kind: "chapter" | "entry"; id: string };
type EntryKind = Entry["kind"];
type EntityForm =
  | { kind: "volume"; id?: string; title: string }
  | { kind: "chapter"; id?: string; title: string; volumeId: string }
  | { kind: "entry"; id?: string; title: string; entryKind: EntryKind; category: string };
type FolderPrompt = { entryId: string; category: string };
type WorkspaceData = {
  volumes: Volume[];
  chapters: Chapter[];
  entries: Entry[];
  changes: z.infer<typeof pendingSchema>[];
};

const emptyData: WorkspaceData = { volumes: [], chapters: [], entries: [], changes: [] };
const SIDEBAR_WIDTH_KEY = "inkwell:sidebar-width";
const DEFAULT_SIDEBAR_WIDTH = 240;
const MIN_SIDEBAR_WIDTH = 200;
const MAX_SIDEBAR_WIDTH = 420;

function clampSidebarWidth(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_SIDEBAR_WIDTH;
  return Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, Math.round(value)));
}
const entryKinds: readonly { value: EntryKind; label: string }[] = [
  { value: "character", label: "人物" },
  { value: "world", label: "世界" },
  { value: "note", label: "笔记" },
];

export function Workspace({
  books,
  booksLoaded,
  booksError,
  activeBook,
  targetBook,
  route,
  searchOpen,
  onSearchOpenChange,
  onOpenBook,
  onRouteChange,
  onRefreshBooks,
  onNewBook,
  onEditBook,
  onDeleteBook,
  onSettings,
}: {
  books: Book[];
  booksLoaded: boolean;
  booksError: string;
  activeBook: Book | null;
  targetBook: Book | null;
  route: AppRoute;
  searchOpen: boolean;
  onSearchOpenChange: (open: boolean) => void;
  onOpenBook: (book: Book, nextRoute?: BookRoute, openSearch?: boolean) => void;
  onRouteChange: (next: AppRoute) => void;
  onRefreshBooks: () => void;
  onNewBook: () => void;
  onEditBook: (book: Book) => void;
  onDeleteBook: (book: Book) => void;
  onSettings: () => void;
}) {
  const book = activeBook ?? targetBook;
  const bookId = book?.id ?? null;
  const [data, setData] = useState<WorkspaceData>(emptyData);
  const [loadedBookId, setLoadedBookId] = useState<string | null>(null);
  const refreshRequest = useRef<() => void>(() => undefined);
  const refresh = useCallback(() => refreshRequest.current(), []);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState<number>(() => {
    try {
      const saved = localStorage.getItem(SIDEBAR_WIDTH_KEY);
      return saved ? clampSidebarWidth(Number(saved)) : DEFAULT_SIDEBAR_WIDTH;
    } catch {
      return DEFAULT_SIDEBAR_WIDTH;
    }
  });
  const [resizing, setResizing] = useState(false);
  const [companionOpen, setCompanionOpen] = useState(true);
  const [sessionSidebar, setSessionSidebar] = useState<HTMLDivElement | null>(null);
  const [focusPending, setFocusPending] = useState<string | null>(null);
  const [form, setForm] = useState<EntityForm | null>(null);
  const [folderPrompt, setFolderPrompt] = useState<FolderPrompt | null>(null);
  const [deletion, setDeletion] = useState<{ path: string; title: string; id: string } | null>(
    null,
  );
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false);
  const [batchMode, setBatchMode] = useState(false);
  const [selectedEntryIds, setSelectedEntryIds] = useState<string[]>([]);
  const [collapsedFolders, setCollapsedFolders] = useState<Record<string, boolean>>({});
  const [dragItem, setDragItem] = useState<{ kind: "chapter" | "entry"; id: string } | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const editor = useRef<EditorHandle>(null);
  const [pendingNavigation, setPendingNavigation] = useState<(() => void) | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<z.infer<typeof searchSchema>[]>([]);
  const [searching, setSearching] = useState(false);
  const [activeHit, setActiveHit] = useState(0);
  const routeRef = useRef(route);
  useEffect(() => {
    routeRef.current = route;
  }, [route]);

  useEvents((event) => {
    if (
      event.type === "resync_required" ||
      (bookId &&
        event.type === "content_changed" &&
        event.book_id === bookId &&
        (event.resource
          ? !["session", "session_deleted", "message", "run"].includes(event.resource)
          : !event.session_id || Boolean(event.run_id)))
    )
      refresh();
  });

  useEffect(() => {
    setSelected(null);
    setBatchMode(false);
    setSelectedEntryIds([]);
    setError("");
    if (!bookId) {
      setData(emptyData);
      setLoadedBookId(null);
      return;
    }
    let disposed = false;
    let running = false;
    let queued = false;
    let initialized = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const currentBookId = bookId;

    async function load() {
      if (running) {
        queued = true;
        return;
      }
      running = true;
      try {
        const [volumes, chapters, entries, changes] = await Promise.all([
          request(`/books/${currentBookId}/volumes`, z.array(volumeSchema)),
          request(`/books/${currentBookId}/chapters`, z.array(chapterSchema)),
          request(`/books/${currentBookId}/entries`, z.array(entrySchema)),
          request(`/books/${currentBookId}/pending-changes?status=pending`, z.array(pendingSchema)),
        ]);
        if (disposed) return;
        volumes.sort((a, b) => a.sort_order - b.sort_order);
        chapters.sort((a, b) => a.sort_order - b.sort_order);
        entries.sort((a, b) => a.sort_order - b.sort_order);
        setData({ volumes, chapters, entries, changes });
        setLoadedBookId(currentBookId);
        if (!initialized) {
          initialized = true;
          if (routeRef.current === "entries") {
            const firstEntry = entries[0];
            if (firstEntry) setSelected((cur) => cur ?? { kind: "entry", id: firstEntry.id });
          } else {
            let lastId: string | null = null;
            try {
              lastId = localStorage.getItem(`inkwell:last-chapter:${currentBookId}`);
            } catch {
              /* Optional preference. */
            }
            const first =
              chapters.find((item) => item.id === lastId) ??
              volumes.flatMap((volume) =>
                chapters.filter((item) => item.volume_id === volume.id),
              )[0] ??
              chapters[0];
            if (first) setSelected((cur) => cur ?? { kind: "chapter", id: first.id });
          }
        }
      } catch (failure) {
        if (!disposed) setError(errorText(failure));
      } finally {
        running = false;
        if (queued && !disposed) {
          queued = false;
          schedule();
        }
      }
    }
    function schedule() {
      if (disposed) return;
      clearTimeout(timer);
      timer = setTimeout(() => void load(), 60);
    }
    refreshRequest.current = schedule;
    void load();
    return () => {
      disposed = true;
      clearTimeout(timer);
      refreshRequest.current = () => undefined;
    };
  }, [bookId]);

  useEffect(() => {
    if (bookId && selected?.kind === "chapter") {
      try {
        localStorage.setItem(`inkwell:last-chapter:${bookId}`, selected.id);
      } catch {
        /* Optional preference. */
      }
    }
  }, [bookId, selected]);

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && bookId) {
        event.preventDefault();
        onSearchOpenChange(!searchOpen);
      }
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [bookId, searchOpen, onSearchOpenChange]);

  useEffect(() => {
    let current = true;
    if (!searchOpen || !query.trim() || !bookId) {
      setHits([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = setTimeout(() => {
      void request(`/books/${bookId}/search?q=${encodeURIComponent(query)}`, z.array(searchSchema))
        .then((results) => {
          if (current) {
            setHits(results);
            setActiveHit(0);
          }
        })
        .catch((failure: unknown) => {
          if (current) setError(errorText(failure));
        })
        .finally(() => {
          if (current) setSearching(false);
        });
    }, 240);
    return () => {
      clearTimeout(timer);
      current = false;
    };
  }, [query, bookId, searchOpen]);

  function navigate(action: () => void) {
    if (editor.current?.dirty) setPendingNavigation(() => action);
    else action();
  }

  function choose(selection: Selection) {
    const targetRoute: BookRoute = selection.kind === "chapter" ? "chapters" : "entries";
    if (selected?.id === selection.id && selected.kind === selection.kind) {
      onRouteChange(targetRoute);
      return;
    }
    navigate(() => {
      setSelected(selection);
      onRouteChange(targetRoute);
    });
  }

  function switchRoute(next: AppRoute) {
    if (next === "shelf") {
      navigate(() => onRouteChange("shelf"));
      return;
    }
    if (!bookId) return;
    setSidebarCollapsed(false);
    if (next !== "entries") {
      setBatchMode(false);
      setSelectedEntryIds([]);
    }
    if (next === "chapters" && selected?.kind !== "chapter") {
      const firstChapter = data.chapters[0];
      if (firstChapter) choose({ kind: "chapter", id: firstChapter.id });
      else
        navigate(() => {
          setSelected(null);
          onRouteChange("chapters");
        });
    } else if (next === "entries" && selected?.kind !== "entry") {
      const firstEntry = data.entries[0];
      if (firstEntry) choose({ kind: "entry", id: firstEntry.id });
      else
        navigate(() => {
          setSelected(null);
          onRouteChange("entries");
        });
    } else {
      onRouteChange(next);
    }
  }

  async function mutate(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      refresh();
      return true;
    } catch (failure) {
      setError(errorText(failure));
      return false;
    } finally {
      setBusy(false);
    }
  }

  function newForm(
    kind: EntityForm["kind"],
    volumeId = "none",
    entryKind: EntryKind = "character",
  ) {
    setError("");
    if (kind === "volume") setForm({ kind: "volume", title: "" });
    else if (kind === "chapter") setForm({ kind: "chapter", title: "", volumeId });
    else setForm({ kind: "entry", title: "", entryKind, category: "" });
  }

  async function submitForm() {
    if (!form || !form.title.trim() || !bookId) return;
    const snapshot = form;
    const path =
      snapshot.kind === "volume" ? "volumes" : snapshot.kind === "chapter" ? "chapters" : "entries";
    const body =
      snapshot.kind === "volume"
        ? {
            title: snapshot.title.trim(),
            sort_order: snapshot.id ? undefined : data.volumes.length,
          }
        : snapshot.kind === "chapter"
          ? {
              title: snapshot.title.trim(),
              ...(snapshot.id
                ? {}
                : {
                    volume_id: snapshot.volumeId === "none" ? null : snapshot.volumeId,
                    sort_order: data.chapters.length,
                  }),
            }
          : {
              title: snapshot.title.trim(),
              ...(snapshot.id
                ? {}
                : {
                    kind: snapshot.entryKind,
                    category: snapshot.category.trim(),
                    visibility: "local",
                    sort_order: data.entries.length,
                  }),
            };
    const result = await mutate(async () => {
      const url = snapshot.id ? `/${path}/${snapshot.id}` : `/books/${bookId}/${path}`;
      const schema = z.object({ id: z.string() });
      const item = await request(url, schema, { method: snapshot.id ? "PATCH" : "POST", body });
      if (!snapshot.id && snapshot.kind !== "volume") {
        choose({ kind: snapshot.kind, id: item.id });
      }
    });
    if (result) setForm(null);
  }

  async function reorderVolumes(id: string, direction: number) {
    const items = data.volumes;
    const index = items.findIndex((item) => item.id === id);
    const target = index + direction;
    if (target < 0 || target >= items.length) return;
    const next = [...items];
    const current = next[index];
    const other = next[target];
    if (!current || !other) return;
    next[index] = other;
    next[target] = current;
    await mutate(() =>
      Promise.all(
        next.map((item, order) =>
          request(`/volumes/${item.id}`, z.object({ id: z.string() }), {
            method: "PATCH",
            body: { sort_order: order },
          }),
        ),
      ),
    );
  }

  async function toggleEntryVisibility(item: Entry) {
    const next = item.visibility === "local" ? "global" : "local";
    setData((cur) => ({
      ...cur,
      entries: cur.entries.map((entry) =>
        entry.id === item.id ? { ...entry, visibility: next } : entry,
      ),
    }));
    const ok = await mutate(() =>
      request(`/entries/${item.id}`, entrySchema, {
        method: "PATCH",
        body: { visibility: next },
      }),
    );
    if (!ok) refresh();
  }

  async function moveEntry(item: Entry, nextKind: EntryKind, nextCategory: string) {
    const cleanCategory = nextCategory.trim();
    if (item.kind === nextKind && item.category.trim() === cleanCategory) return;
    await mutate(() =>
      request(`/entries/${item.id}`, entrySchema, {
        method: "PATCH",
        body: { kind: nextKind, category: cleanCategory },
      }),
    );
  }

  async function moveChapterToVolume(item: Chapter, nextVolumeId: string | null) {
    if (item.volume_id === nextVolumeId) return;
    await mutate(() =>
      request(`/chapters/${item.id}`, chapterSchema, {
        method: "PATCH",
        body: { volume_id: nextVolumeId },
      }),
    );
  }

  async function dropEntryOnEntry(dragId: string, target: Entry) {
    if (dragId === target.id) return;
    const dragged = data.entries.find((item) => item.id === dragId);
    if (!dragged) return;
    const targetCategory = target.category.trim();
    const bucket = data.entries.filter(
      (item) =>
        item.id !== dragId && item.kind === target.kind && item.category.trim() === targetCategory,
    );
    const targetIndex = bucket.findIndex((item) => item.id === target.id);
    const insertAt = targetIndex >= 0 ? targetIndex : bucket.length;
    const updatedDragged: Entry = { ...dragged, kind: target.kind, category: targetCategory };
    bucket.splice(insertAt, 0, updatedDragged);
    await mutate(() =>
      Promise.all(
        bucket.map((item, order) =>
          request(`/entries/${item.id}`, entrySchema, {
            method: "PATCH",
            body:
              item.id === dragId
                ? { kind: target.kind, category: targetCategory, sort_order: order }
                : { sort_order: order },
          }),
        ),
      ),
    );
  }

  async function dropChapterOnChapter(dragId: string, target: Chapter) {
    if (dragId === target.id) return;
    const dragged = data.chapters.find((item) => item.id === dragId);
    if (!dragged) return;
    const bucket = data.chapters.filter(
      (item) => item.id !== dragId && item.volume_id === target.volume_id,
    );
    const targetIndex = bucket.findIndex((item) => item.id === target.id);
    const insertAt = targetIndex >= 0 ? targetIndex : bucket.length;
    bucket.splice(insertAt, 0, { ...dragged, volume_id: target.volume_id });
    await mutate(() =>
      Promise.all(
        bucket.map((item, order) =>
          request(`/chapters/${item.id}`, chapterSchema, {
            method: "PATCH",
            body:
              item.id === dragId
                ? { volume_id: target.volume_id, sort_order: order }
                : { sort_order: order },
          }),
        ),
      ),
    );
  }

  async function applyBatchVisibility(visibility: "local" | "global") {
    if (selectedEntryIds.length === 0) return;
    const ids = [...selectedEntryIds];
    const ok = await mutate(() =>
      Promise.all(
        ids.map((id) =>
          request(`/entries/${id}`, entrySchema, { method: "PATCH", body: { visibility } }),
        ),
      ),
    );
    if (ok) {
      setSelectedEntryIds([]);
      setBatchMode(false);
    }
  }

  async function applyBatchMove(nextKind: EntryKind, nextCategory: string) {
    if (selectedEntryIds.length === 0) return;
    const ids = [...selectedEntryIds];
    const cleanCategory = nextCategory.trim();
    const ok = await mutate(() =>
      Promise.all(
        ids.map((id) =>
          request(`/entries/${id}`, entrySchema, {
            method: "PATCH",
            body: { kind: nextKind, category: cleanCategory },
          }),
        ),
      ),
    );
    if (ok) {
      setSelectedEntryIds([]);
      setBatchMode(false);
    }
  }

  async function applyBatchDelete() {
    if (selectedEntryIds.length === 0) return;
    const ids = [...selectedEntryIds];
    const ok = await mutate(async () => {
      for (const id of ids) {
        if (editor.current?.dirty && selected?.id === id && !editor.current.stash()) {
          throw new Error("请先保存当前草稿。");
        }
        await remove(`/entries/${id}`);
      }
      if (selected && ids.includes(selected.id)) setSelected(null);
    });
    if (ok) {
      setBatchDeleteOpen(false);
      setSelectedEntryIds([]);
      setBatchMode(false);
    }
  }

  function toggleEntrySelection(id: string) {
    setSelectedEntryIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }

  const loaded = bookId !== null && loadedBookId === bookId;
  const chapter =
    selected?.kind === "chapter"
      ? data.chapters.find((item) => item.id === selected.id)
      : undefined;
  const entry =
    selected?.kind === "entry" ? data.entries.find((item) => item.id === selected.id) : undefined;
  const documentValue = chapter ?? entry ?? null;

  async function save(value: DocumentValue, baseline: DocumentValue) {
    if (!selected) return;
    if (selected.kind === "chapter") {
      const latest = await request(`/chapters/${selected.id}`, chapterSchema);
      if (latest.content !== baseline.content || latest.title !== baseline.title)
        throw new ApiError(409, "章节已在别处更新，未保存的修改已暂存，请对照后再重新载入。");
      const updated = await request(`/chapters/${selected.id}`, chapterSchema, {
        method: "PATCH",
        body: value,
      });
      setData((cur) => ({
        ...cur,
        chapters: cur.chapters.map((item) => (item.id === updated.id ? updated : item)),
      }));
    } else {
      const latest = await request(`/entries/${selected.id}`, entrySchema);
      if (latest.content !== baseline.content || latest.title !== baseline.title)
        throw new ApiError(409, "设定已在别处更新，未保存的修改已暂存，请对照后再重新载入。");
      const updated = await request(`/entries/${selected.id}`, entrySchema, {
        method: "PATCH",
        body: value,
      });
      setData((cur) => ({
        ...cur,
        entries: cur.entries.map((item) => (item.id === updated.id ? updated : item)),
      }));
    }
  }

  function categoriesFor(kind: EntryKind) {
    return Array.from(
      new Set(
        data.entries
          .filter((item) => item.kind === kind)
          .map((item) => item.category.trim())
          .filter(Boolean),
      ),
    );
  }

  function renderChapterRow(item: Chapter) {
    const dropKey = `chapter:${item.id}`;
    return (
      <div
        key={item.id}
        className={`directory-row ${selected?.id === item.id ? "is-selected" : ""} ${dropTarget === dropKey ? "is-drop-target" : ""}`}
        draggable
        onDragStart={(event) => {
          event.dataTransfer.effectAllowed = "move";
          setDragItem({ kind: "chapter", id: item.id });
        }}
        onDragOver={(event) => {
          if (dragItem?.kind === "chapter" && dragItem.id !== item.id) {
            event.preventDefault();
            setDropTarget(dropKey);
          }
        }}
        onDragLeave={() => {
          if (dropTarget === dropKey) setDropTarget(null);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDropTarget(null);
          if (dragItem?.kind === "chapter") void dropChapterOnChapter(dragItem.id, item);
          setDragItem(null);
        }}
        onDragEnd={() => {
          setDragItem(null);
          setDropTarget(null);
        }}
      >
        <span className="directory-drag-handle" aria-hidden="true">
          <GripVertical size={13} />
        </span>
        <Button
          variant="ghost"
          className="directory-title"
          onClick={() => choose({ kind: "chapter", id: item.id })}
        >
          <span>{item.title}</span>
        </Button>
        <div className="directory-actions">
          <Menu label={`${item.title}的操作`} iconOnly>
            <MenuSub label="移动到分卷" icon={<FolderInput size={14} />}>
              <MenuItem onSelect={() => void moveChapterToVolume(item, null)}>
                <Check
                  size={13}
                  className={item.volume_id === null ? "opacity-100" : "opacity-0"}
                />
                不分卷
              </MenuItem>
              {data.volumes.map((volume) => (
                <MenuItem
                  key={volume.id}
                  onSelect={() => void moveChapterToVolume(item, volume.id)}
                >
                  <Check
                    size={13}
                    className={item.volume_id === volume.id ? "opacity-100" : "opacity-0"}
                  />
                  {volume.title}
                </MenuItem>
              ))}
            </MenuSub>
            <MenuItem
              onSelect={() =>
                navigate(() =>
                  setForm({
                    kind: "chapter",
                    id: item.id,
                    title: item.title,
                    volumeId: item.volume_id ?? "none",
                  }),
                )
              }
            >
              <Pencil size={14} />
              重命名
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              danger
              onSelect={() =>
                setDeletion({ path: `/chapters/${item.id}`, title: item.title, id: item.id })
              }
            >
              <Trash2 size={14} />
              删除
            </MenuItem>
          </Menu>
        </div>
      </div>
    );
  }

  function renderEntryRow(item: Entry) {
    const checked = selectedEntryIds.includes(item.id);
    const dropKey = `entry:${item.id}`;
    const foldersInKind = categoriesFor(item.kind);
    return (
      <div
        className={`directory-row ${selected?.id === item.id ? "is-selected" : ""} ${dropTarget === dropKey ? "is-drop-target" : ""}`}
        key={item.id}
        draggable={!batchMode}
        onDragStart={(event) => {
          event.dataTransfer.effectAllowed = "move";
          setDragItem({ kind: "entry", id: item.id });
        }}
        onDragOver={(event) => {
          if (dragItem?.kind === "entry" && dragItem.id !== item.id) {
            event.preventDefault();
            setDropTarget(dropKey);
          }
        }}
        onDragLeave={() => {
          if (dropTarget === dropKey) setDropTarget(null);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDropTarget(null);
          if (dragItem?.kind === "entry") void dropEntryOnEntry(dragItem.id, item);
          setDragItem(null);
        }}
        onDragEnd={() => {
          setDragItem(null);
          setDropTarget(null);
        }}
      >
        {batchMode ? (
          <label className="directory-check">
            <input
              type="checkbox"
              checked={checked}
              aria-label={`选择 ${item.title}`}
              onChange={() => toggleEntrySelection(item.id)}
            />
          </label>
        ) : (
          <span className="directory-drag-handle" aria-hidden="true">
            <GripVertical size={13} />
          </span>
        )}
        <Button
          variant="ghost"
          className="directory-title"
          onClick={(event) => {
            if (event.ctrlKey || event.metaKey) {
              setBatchMode(true);
              toggleEntrySelection(item.id);
            } else if (batchMode) {
              toggleEntrySelection(item.id);
            } else {
              choose({ kind: "entry", id: item.id });
            }
          }}
        >
          <span>{item.title}</span>
        </Button>
        {!batchMode && (
          <>
            <button
              type="button"
              className={`entry-vis-btn ${item.visibility === "global" ? "is-global" : "is-local"}`}
              disabled={busy}
              title={
                item.visibility === "global"
                  ? "仅全局资料会话可见，点击改为公开"
                  : "所有会话可见，点击改为仅全局"
              }
              aria-label={`${item.title} 可见性：${item.visibility === "global" ? "仅全局" : "公开"}`}
              onClick={(event) => {
                event.stopPropagation();
                void toggleEntryVisibility(item);
              }}
            >
              {item.visibility === "global" ? <Sparkles size={11} /> : <Eye size={11} />}
              <span>{item.visibility === "global" ? "仅全局" : "公开"}</span>
            </button>
            <div className="directory-actions">
              <Menu label={`${item.title}的操作`} iconOnly>
                <MenuSub label="移动到…" icon={<FolderInput size={14} />}>
                  {foldersInKind.map((folderName) => (
                    <MenuItem
                      key={folderName}
                      onSelect={() => void moveEntry(item, item.kind, folderName)}
                    >
                      <Check
                        size={13}
                        className={
                          item.category.trim() === folderName ? "opacity-100" : "opacity-0"
                        }
                      />
                      {folderName}
                    </MenuItem>
                  ))}
                  {item.category.trim() && (
                    <MenuItem onSelect={() => void moveEntry(item, item.kind, "")}>
                      <Folder size={13} />
                      移出文件夹
                    </MenuItem>
                  )}
                  <MenuSeparator />
                  <MenuItem onSelect={() => setFolderPrompt({ entryId: item.id, category: "" })}>
                    <FolderPlus size={13} />
                    新建文件夹…
                  </MenuItem>
                  <MenuSeparator />
                  {entryKinds
                    .filter((option) => option.value !== item.kind)
                    .map((option) => (
                      <MenuItem
                        key={option.value}
                        onSelect={() => void moveEntry(item, option.value, "")}
                      >
                        移至{option.label}
                      </MenuItem>
                    ))}
                </MenuSub>
                <MenuItem
                  onSelect={() =>
                    setForm({
                      kind: "entry",
                      id: item.id,
                      title: item.title,
                      entryKind: item.kind,
                      category: item.category,
                    })
                  }
                >
                  <Pencil size={14} />
                  重命名
                </MenuItem>
                <MenuSeparator />
                <MenuItem
                  danger
                  onSelect={() =>
                    setDeletion({ path: `/entries/${item.id}`, title: item.title, id: item.id })
                  }
                >
                  <Trash2 size={14} />
                  删除
                </MenuItem>
              </Menu>
            </div>
          </>
        )}
      </div>
    );
  }

  const currentPending =
    data.changes.find((change) => change.id === focusPending) ?? data.changes[0];
  const editing = route === "chapters" || route === "entries";
  const sidebarClosed = route === "shelf" || sidebarCollapsed;

  const sidebarToggle = (
    <Button
      variant="ghost"
      className="icon-control"
      title={sidebarCollapsed ? "展开目录" : "收起目录"}
      aria-label={sidebarCollapsed ? "展开目录" : "收起目录"}
      onClick={() => setSidebarCollapsed((value) => !value)}
    >
      {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
    </Button>
  );

  const companionToggle = (
    <Button
      variant="ghost"
      className="icon-control"
      title={companionOpen ? "收起 AI 面板" : "展开 AI 面板"}
      aria-label={companionOpen ? "收起 AI 面板" : "展开 AI 面板"}
      onClick={() => setCompanionOpen((value) => !value)}
    >
      {companionOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
    </Button>
  );

  const updateSidebarWidth = useCallback((nextWidth: number) => {
    const clamped = clampSidebarWidth(nextWidth);
    setSidebarWidth(clamped);
    try {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(clamped));
    } catch {
      /* Optional preference. */
    }
    return clamped;
  }, []);

  const startSidebarResize = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (sidebarClosed) return;
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = sidebarWidth;
      let latestWidth = startWidth;
      setResizing(true);

      const onMove = (moveEvent: PointerEvent) => {
        latestWidth = clampSidebarWidth(startWidth + (moveEvent.clientX - startX));
        setSidebarWidth(latestWidth);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        setResizing(false);
        updateSidebarWidth(latestWidth);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [sidebarClosed, sidebarWidth, updateSidebarWidth],
  );

  return (
    <div
      className={`app-shell ${sidebarClosed ? "sidebar-collapsed" : ""} ${resizing ? "is-resizing" : ""}`}
      style={
        !sidebarClosed
          ? { gridTemplateColumns: `48px ${sidebarWidth}px minmax(0, 1fr)` }
          : undefined
      }
    >
      <nav className="activity-rail" aria-label="主导航">
        <Button
          variant="ghost"
          className={`activity-button ${route === "shelf" ? "is-active" : ""}`}
          title="书架"
          aria-label="书架"
          aria-pressed={route === "shelf"}
          onClick={() => switchRoute("shelf")}
        >
          <LibraryBig size={21} />
        </Button>
        <div className="activity-primary">
          <Button
            variant="ghost"
            className={`activity-button ${route === "chapters" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `章节 · ${book.title}` : "请先新建作品"}
            aria-label="章节"
            aria-pressed={route === "chapters"}
            onClick={() => switchRoute("chapters")}
          >
            <BookOpen size={21} />
          </Button>
          <Button
            variant="ghost"
            className={`activity-button ${route === "entries" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `设定 · ${book.title}` : "请先新建作品"}
            aria-label="设定"
            aria-pressed={route === "entries"}
            onClick={() => switchRoute("entries")}
          >
            <NotebookPen size={21} />
          </Button>
          <Button
            variant="ghost"
            className={`activity-button ${route === "discussion" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `讨论 · ${book.title}` : "请先新建作品"}
            aria-label="讨论"
            aria-pressed={route === "discussion"}
            onClick={() => switchRoute("discussion")}
          >
            <MessagesSquare size={21} />
          </Button>
          <Button
            variant="ghost"
            className={`activity-button ${route === "pending" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `待审 · ${book.title}` : "请先新建作品"}
            aria-label="待审"
            aria-pressed={route === "pending"}
            onClick={() => switchRoute("pending")}
          >
            <ListChecks size={21} />
            {data.changes.length > 0 && (
              <span className="activity-count">
                {data.changes.length > 99 ? "99+" : data.changes.length}
              </span>
            )}
          </Button>
          <Button
            variant="ghost"
            className="activity-button"
            disabled={!book}
            title={book ? "搜索 · Ctrl+K" : "请先新建作品"}
            aria-label="搜索"
            onClick={() => {
              if (!book) return;
              if (route === "shelf") onOpenBook(book, "chapters", true);
              else onSearchOpenChange(true);
            }}
          >
            <Search size={21} />
          </Button>
        </div>
        <div className="activity-bottom">
          <Button
            variant="ghost"
            className="activity-button"
            title="设置"
            aria-label="设置"
            onClick={onSettings}
          >
            <Settings2 size={21} />
          </Button>
        </div>
      </nav>

      <aside className="directory-sidebar" aria-hidden={sidebarClosed} aria-label="作品目录">
        <header className="directory-heading">
          <h1 className="directory-book-title" title={book?.title ?? ""}>
            {book?.title ?? ""}
          </h1>
        </header>
        <div className="directory-scroll">
          {route === "chapters" && (
            <>
              <div className="directory-section-heading">
                <h2>章节</h2>
                <Menu label="新建" iconOnly>
                  <MenuItem onSelect={() => newForm("volume")}>
                    <FolderPlus size={14} />
                    新建分卷
                  </MenuItem>
                  <MenuItem onSelect={() => newForm("chapter")}>
                    <FilePlus2 size={14} />
                    新建章节
                  </MenuItem>
                </Menu>
              </div>
              {data.volumes.map((volume, index) => {
                const dropKey = `volume:${volume.id}`;
                return (
                  <section className="directory-group" key={volume.id}>
                    <div
                      className={`directory-group-title ${dropTarget === dropKey ? "is-drop-target" : ""}`}
                      onDragOver={(event) => {
                        if (dragItem?.kind === "chapter") {
                          event.preventDefault();
                          setDropTarget(dropKey);
                        }
                      }}
                      onDragLeave={() => {
                        if (dropTarget === dropKey) setDropTarget(null);
                      }}
                      onDrop={(event) => {
                        event.preventDefault();
                        setDropTarget(null);
                        if (dragItem?.kind === "chapter") {
                          const ch = data.chapters.find((c) => c.id === dragItem.id);
                          if (ch) void moveChapterToVolume(ch, volume.id);
                        }
                        setDragItem(null);
                      }}
                    >
                      <Folder size={14} />
                      <h3>{volume.title}</h3>
                      <div className="directory-actions">
                        <Button
                          variant="ghost"
                          className="icon-control"
                          title={`在 ${volume.title} 新建章节`}
                          aria-label={`在 ${volume.title} 新建章节`}
                          onClick={() => newForm("chapter", volume.id)}
                        >
                          <Plus size={14} />
                        </Button>
                        <Menu label={`${volume.title}的操作`} iconOnly>
                          <MenuItem
                            disabled={busy || index === 0}
                            onSelect={() => void reorderVolumes(volume.id, -1)}
                          >
                            <ArrowUp size={14} />
                            上移
                          </MenuItem>
                          <MenuItem
                            disabled={busy || index === data.volumes.length - 1}
                            onSelect={() => void reorderVolumes(volume.id, 1)}
                          >
                            <ArrowDown size={14} />
                            下移
                          </MenuItem>
                          <MenuItem
                            onSelect={() =>
                              setForm({ kind: "volume", id: volume.id, title: volume.title })
                            }
                          >
                            <Pencil size={14} />
                            重命名
                          </MenuItem>
                          <MenuSeparator />
                          <MenuItem
                            danger
                            onSelect={() =>
                              setDeletion({
                                path: `/volumes/${volume.id}`,
                                title: volume.title,
                                id: volume.id,
                              })
                            }
                          >
                            <Trash2 size={14} />
                            删除
                          </MenuItem>
                        </Menu>
                      </div>
                    </div>
                    {data.chapters
                      .filter((item) => item.volume_id === volume.id)
                      .map((item) => renderChapterRow(item))}
                  </section>
                );
              })}
              {data.chapters
                .filter((item) => item.volume_id === null)
                .map((item) => renderChapterRow(item))}
              {loaded && data.chapters.length === 0 && (
                <EmptyState title="暂无章节">
                  <Button onClick={() => newForm("chapter")}>
                    <Plus size={14} />
                    新建章节
                  </Button>
                </EmptyState>
              )}
            </>
          )}

          {route === "entries" && (
            <>
              <div className="directory-section-heading">
                <h2>设定</h2>
                {data.entries.length > 0 && (
                  <Button
                    variant="ghost"
                    className={`directory-manage-btn ${batchMode ? "is-active" : ""}`}
                    aria-pressed={batchMode}
                    onClick={() => {
                      setBatchMode((value) => {
                        if (value) setSelectedEntryIds([]);
                        return !value;
                      });
                    }}
                  >
                    {batchMode ? "完成" : "管理"}
                  </Button>
                )}
              </div>
              {entryKinds.map(({ value: kind, label }) => {
                const items = data.entries.filter((item) => item.kind === kind);
                const uncategorized = items.filter((item) => !item.category.trim());
                const folders = new Map<string, Entry[]>();
                for (const item of items) {
                  const cat = item.category.trim();
                  if (!cat) continue;
                  const bucket = folders.get(cat);
                  if (bucket) bucket.push(item);
                  else folders.set(cat, [item]);
                }
                const groupDropKey = `group:${kind}`;
                return (
                  <section className="directory-group" key={kind}>
                    <div
                      className={`directory-group-title ${dropTarget === groupDropKey ? "is-drop-target" : ""}`}
                      onDragOver={(event) => {
                        if (dragItem?.kind === "entry") {
                          event.preventDefault();
                          setDropTarget(groupDropKey);
                        }
                      }}
                      onDragLeave={() => {
                        if (dropTarget === groupDropKey) setDropTarget(null);
                      }}
                      onDrop={(event) => {
                        event.preventDefault();
                        setDropTarget(null);
                        if (dragItem?.kind === "entry") {
                          const dragged = data.entries.find((e) => e.id === dragItem.id);
                          if (dragged) void moveEntry(dragged, kind, "");
                        }
                        setDragItem(null);
                      }}
                    >
                      <h3>{label}</h3>
                      <Button
                        variant="ghost"
                        className="icon-control"
                        title={`新建${label}`}
                        aria-label={`新建${label}`}
                        onClick={() => newForm("entry", "none", kind)}
                      >
                        <Plus size={14} />
                      </Button>
                    </div>
                    {[...folders.entries()].map(([folderName, folderItems]) => {
                      const folderKey = `folder:${kind}:${folderName}`;
                      const collapsed = Boolean(collapsedFolders[folderKey]);
                      return (
                        <div className="directory-folder" key={folderKey}>
                          <button
                            type="button"
                            className={`directory-folder-toggle ${dropTarget === folderKey ? "is-drop-target" : ""}`}
                            aria-expanded={!collapsed}
                            onClick={() =>
                              setCollapsedFolders((current) => ({
                                ...current,
                                [folderKey]: !collapsed,
                              }))
                            }
                            onDragOver={(event) => {
                              if (dragItem?.kind === "entry") {
                                event.preventDefault();
                                setDropTarget(folderKey);
                              }
                            }}
                            onDragLeave={() => {
                              if (dropTarget === folderKey) setDropTarget(null);
                            }}
                            onDrop={(event) => {
                              event.preventDefault();
                              setDropTarget(null);
                              if (dragItem?.kind === "entry") {
                                const dragged = data.entries.find((e) => e.id === dragItem.id);
                                if (dragged) void moveEntry(dragged, kind, folderName);
                              }
                              setDragItem(null);
                            }}
                          >
                            {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                            <Folder size={13} />
                            <span>{folderName}</span>
                            <span className="directory-folder-count">{folderItems.length}</span>
                          </button>
                          {!collapsed && (
                            <div className="directory-folder-items">
                              {folderItems.map((item) => renderEntryRow(item))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                    {uncategorized.map((item) => renderEntryRow(item))}
                    {items.length === 0 && <p className="directory-empty">暂无条目</p>}
                  </section>
                );
              })}
            </>
          )}

          <div
            ref={setSessionSidebar}
            hidden={route !== "discussion"}
            className="session-sidebar-target"
          />

          {route === "pending" && (
            <>
              <div className="directory-section-heading">
                <h2>待审变更</h2>
                <span className="directory-meta">{data.changes.length}</span>
              </div>
              {data.changes.map((change) => (
                <Button
                  key={change.id}
                  variant="ghost"
                  className={`pending-directory-item ${currentPending?.id === change.id ? "is-selected" : ""}`}
                  onClick={() => setFocusPending(change.id)}
                >
                  <span>{change.title || "正文变更"}</span>
                  <small>
                    {change.action === "create"
                      ? "新增"
                      : change.action === "delete"
                        ? "删除"
                        : "修改"}
                  </small>
                </Button>
              ))}
              {data.changes.length === 0 && <p className="directory-empty">暂无待审变更</p>}
            </>
          )}
        </div>

        {route === "entries" && batchMode && (
          <div className="directory-batch-bar">
            <div className="directory-batch-meta">
              <span>已选 {selectedEntryIds.length} 项</span>
              <button
                type="button"
                className="text-xs text-zinc-500 hover:text-zinc-900"
                onClick={() =>
                  setSelectedEntryIds(
                    selectedEntryIds.length === data.entries.length
                      ? []
                      : data.entries.map((item) => item.id),
                  )
                }
              >
                {selectedEntryIds.length === data.entries.length ? "清空" : "全选"}
              </button>
            </div>
            <div className="directory-batch-actions">
              <Button
                disabled={busy || selectedEntryIds.length === 0}
                onClick={() => void applyBatchVisibility("local")}
              >
                设为公开
              </Button>
              <Button
                disabled={busy || selectedEntryIds.length === 0}
                onClick={() => void applyBatchVisibility("global")}
              >
                设为仅全局
              </Button>
              <Menu label="移动到…" triggerClassName="button-secondary">
                {entryKinds.map(({ value: kind, label }) => {
                  const folders = categoriesFor(kind);
                  return (
                    <MenuSub key={kind} label={label}>
                      <MenuItem onSelect={() => void applyBatchMove(kind, "")}>
                        直接放到{label}下
                      </MenuItem>
                      {folders.length > 0 && <MenuSeparator />}
                      {folders.map((folderName) => (
                        <MenuItem
                          key={folderName}
                          onSelect={() => void applyBatchMove(kind, folderName)}
                        >
                          <Folder size={13} />
                          {folderName}
                        </MenuItem>
                      ))}
                    </MenuSub>
                  );
                })}
              </Menu>
              <Button
                variant="danger"
                disabled={busy || selectedEntryIds.length === 0}
                onClick={() => setBatchDeleteOpen(true)}
              >
                删除
              </Button>
            </div>
          </div>
        )}
        <div
          className="sidebar-resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整侧边栏宽度"
          title="拖动调整宽度（双击恢复默认）"
          onPointerDown={startSidebarResize}
          onDoubleClick={() => updateSidebarWidth(DEFAULT_SIDEBAR_WIDTH)}
        />
      </aside>

      <div
        className={`workspace-body ${
          route === "shelf"
            ? "shelf-view"
            : route === "discussion"
              ? "discussion-view"
              : route === "pending"
                ? "pending-view"
                : editing && companionOpen
                  ? "with-companion"
                  : ""
        }`}
      >
        <main
          className={`workspace-document ${route === "discussion" ? "is-collapsed" : ""}`}
          aria-hidden={route === "discussion"}
        >
          <Alert>{route === "shelf" ? booksError : error}</Alert>

          {route === "shelf" && (
            <div className="shelf-scroll">
              <div className="shelf">
                <header className="shelf-header">
                  <div className="shelf-heading">
                    <div className="shelf-brand">
                      <img src="/inkwell.svg" alt="" className="shelf-logo" />
                      <h1>书架</h1>
                    </div>
                  </div>
                  <div className="shelf-actions">
                    <Button variant="primary" onClick={onNewBook}>
                      <Plus size={16} />
                      新建作品
                    </Button>
                  </div>
                </header>
                <div className="shelf-main">
                  {!booksLoaded ? (
                    <EmptyState title={booksError ? "暂时无法读取书架" : "正在打开书架…"}>
                      <Button onClick={onRefreshBooks}>重新加载</Button>
                    </EmptyState>
                  ) : books.length === 0 ? (
                    <EmptyState title="暂无作品" />
                  ) : (
                    <div className="book-grid">
                      {books.map((item) => (
                        <article
                          className="book-card-surface"
                          key={item.id}
                          role="button"
                          tabIndex={0}
                          aria-label={`打开作品 ${item.title}`}
                          onClick={() => onOpenBook(item)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              onOpenBook(item);
                            }
                          }}
                        >
                          <div className="book-cover">
                            {item.cover_path ? (
                              <img
                                className="book-cover-image"
                                src={`/api/books/${item.id}/cover?v=${encodeURIComponent(item.updated_at)}`}
                                alt={item.title}
                              />
                            ) : (
                              <div className="book-cover-frame">
                                <BookOpen size={30} />
                                <span className="book-cover-title">{item.title}</span>
                              </div>
                            )}
                          </div>
                          <div className="book-card-details">
                            <span className="book-card-title">{item.title}</span>
                            {item.description && (
                              <p className="book-card-desc">{item.description}</p>
                            )}
                            <div className="book-card-footer">
                              <span className="book-card-date">
                                {new Date(item.updated_at).toLocaleDateString("zh-CN")}
                              </span>
                              <div
                                className="book-card-menu"
                                onClick={(event) => event.stopPropagation()}
                                onKeyDown={(event) => event.stopPropagation()}
                              >
                                <Menu label={`管理 ${item.title}`} iconOnly>
                                  <MenuItem onSelect={() => onEditBook(item)}>
                                    <Pencil size={14} />
                                    编辑作品
                                  </MenuItem>
                                  <MenuSeparator />
                                  <MenuItem danger onSelect={() => onDeleteBook(item)}>
                                    <Trash2 size={14} />
                                    删除作品
                                  </MenuItem>
                                </Menu>
                              </div>
                            </div>
                          </div>
                        </article>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {editing && (
            <div className="document-content">
              {documentValue && selected && bookId ? (
                <Editor
                  key={`${selected.kind}:${selected.id}`}
                  documentKey={`${bookId}:${selected.kind}:${selected.id}`}
                  value={documentValue}
                  kind={selected.kind === "chapter" ? "章节" : "设定"}
                  onSave={save}
                  ref={editor}
                  active={editing}
                  toolbarStart={sidebarToggle}
                  toolbarEnd={companionToggle}
                />
              ) : (
                <>
                  <div className="document-empty-toolbar">
                    {sidebarToggle}
                    {companionToggle}
                  </div>
                  <EmptyState
                    title={loaded ? (route === "entries" ? "暂无设定" : "暂无章节") : "正在载入"}
                  >
                    {loaded && (
                      <Button onClick={() => newForm(route === "entries" ? "entry" : "chapter")}>
                        <Plus size={14} />
                        {route === "entries" ? "新建设定" : "新建章节"}
                      </Button>
                    )}
                  </EmptyState>
                </>
              )}
            </div>
          )}

          {route === "pending" && (
            <div className="pending-content">
              <PendingPanel
                showHeading={false}
                toolbarStart={sidebarToggle}
                changes={currentPending ? [currentPending] : []}
                onChanged={refresh}
                focusedId={currentPending?.id ?? null}
                beforeApply={(change) =>
                  !(editor.current?.dirty && change.target_id === selected?.id)
                }
              />
            </div>
          )}
        </main>

        <div
          className={`workspace-chat ${
            route !== "discussion" && !(editing && companionOpen) ? "is-collapsed" : ""
          }`}
          aria-hidden={route !== "discussion" && !(editing && companionOpen)}
        >
          {bookId && (
            <ChatPanel
              key={bookId}
              bookId={bookId}
              expanded={route === "discussion"}
              toolbarStart={sidebarToggle}
              sidebarTarget={sessionSidebar}
              onPending={(id) => {
                setFocusPending(id);
                onRouteChange("pending");
                setSidebarCollapsed(false);
              }}
            />
          )}
        </div>
      </div>

      <DialogPrimitive.Root open={searchOpen} onOpenChange={onSearchOpenChange}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="dialog-overlay" />
          <DialogPrimitive.Content className="spotlight-dialog" aria-describedby={undefined}>
            <DialogPrimitive.Title className="sr-only">搜索全书</DialogPrimitive.Title>
            <div className="spotlight-bar">
              <Search size={18} />
              <input
                className="spotlight-input"
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (hits.length === 0) return;
                  if (event.key === "ArrowDown") {
                    event.preventDefault();
                    setActiveHit((current) => (current + 1) % hits.length);
                  } else if (event.key === "ArrowUp") {
                    event.preventDefault();
                    setActiveHit((current) => (current - 1 + hits.length) % hits.length);
                  } else if (event.key === "Enter") {
                    const hit = hits[activeHit] ?? hits[0];
                    if (hit) {
                      event.preventDefault();
                      onSearchOpenChange(false);
                      choose({ kind: hit.target_type, id: hit.target_id });
                    }
                  }
                }}
                placeholder="搜索章节、人物、世界观与笔记…"
                aria-label="搜索全书"
              />
              <kbd className="spotlight-kbd">Esc</kbd>
            </div>
            <div className="spotlight-results">
              {!query.trim() ? (
                <p className="spotlight-hint">输入关键词检索全书 · ↑↓ 切换 · Enter 打开</p>
              ) : searching ? (
                <p className="spotlight-hint">正在搜索…</p>
              ) : hits.length === 0 ? (
                <p className="spotlight-hint">没有找到相关内容</p>
              ) : (
                hits.map((hit, index) => (
                  <button
                    type="button"
                    key={`${hit.target_type}:${hit.target_id}`}
                    className={`spotlight-hit ${index === activeHit ? "is-active" : ""}`}
                    onMouseEnter={() => setActiveHit(index)}
                    onClick={() => {
                      onSearchOpenChange(false);
                      choose({ kind: hit.target_type, id: hit.target_id });
                    }}
                  >
                    {hit.target_type === "chapter" ? (
                      <BookOpen size={16} />
                    ) : (
                      <NotebookPen size={16} />
                    )}
                    <div className="spotlight-hit-body">
                      <div className="spotlight-hit-top">
                        <strong>{hit.title}</strong>
                        <span className="spotlight-hit-kind">
                          {hit.target_type === "chapter" ? "章节" : "设定"}
                        </span>
                      </div>
                      <small>{hit.snippet.replace(/<[^>]*>/g, "")}</small>
                    </div>
                  </button>
                ))
              )}
            </div>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>

      <Dialog
        open={form !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setForm(null);
        }}
        title={`${form?.id ? "重命名" : "新建"}${
          form?.kind === "volume" ? "分卷" : form?.kind === "chapter" ? "章节" : "设定"
        }`}
      >
        <Alert>{error}</Alert>
        {form && (
          <div className="stack">
            <Input
              label="名称"
              autoFocus
              value={form.title}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
            />
            {form.kind === "chapter" && !form.id && (
              <Select
                label="所属分卷"
                value={form.volumeId}
                onChange={(value) => setForm({ ...form, volumeId: value })}
                options={[
                  { value: "none", label: "不分卷" },
                  ...data.volumes.map((volume) => ({ value: volume.id, label: volume.title })),
                ]}
              />
            )}
            {form.kind === "entry" && !form.id && (
              <>
                <Select
                  label="设定类型"
                  value={form.entryKind}
                  onChange={(value) =>
                    setForm({ ...form, entryKind: entrySchema.shape.kind.parse(value) })
                  }
                  options={entryKinds.map((item) => ({
                    value: item.value,
                    label: item.value === "world" ? "世界观" : item.label,
                  }))}
                />
                <Input
                  label="文件夹（可选，仅一层）"
                  value={form.category}
                  list={`category-options-${form.entryKind}`}
                  placeholder="留空则直接放在分组下"
                  onChange={(event) => setForm({ ...form, category: event.target.value })}
                />
                <datalist id={`category-options-${form.entryKind}`}>
                  {categoriesFor(form.entryKind).map((category) => (
                    <option key={category} value={category} />
                  ))}
                </datalist>
              </>
            )}
            <div className="dialog-actions">
              <Button disabled={busy} onClick={() => setForm(null)}>
                取消
              </Button>
              <Button
                variant="primary"
                disabled={busy || !form.title.trim()}
                onClick={() => void submitForm()}
              >
                {busy ? "保存中" : "确认"}
              </Button>
            </div>
          </div>
        )}
      </Dialog>

      <Dialog
        open={folderPrompt !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setFolderPrompt(null);
        }}
        title="新建文件夹并移动"
      >
        <Alert>{error}</Alert>
        {folderPrompt && (
          <div className="stack">
            <Input
              label="文件夹名称"
              autoFocus
              value={folderPrompt.category}
              onChange={(event) =>
                setFolderPrompt({ ...folderPrompt, category: event.target.value })
              }
            />
            <div className="dialog-actions">
              <Button disabled={busy} onClick={() => setFolderPrompt(null)}>
                取消
              </Button>
              <Button
                variant="primary"
                disabled={busy || !folderPrompt.category.trim()}
                onClick={() => {
                  const targetEntry = data.entries.find((e) => e.id === folderPrompt.entryId);
                  if (!targetEntry) return;
                  void moveEntry(targetEntry, targetEntry.kind, folderPrompt.category).then(() =>
                    setFolderPrompt(null),
                  );
                }}
              >
                确认移动
              </Button>
            </div>
          </div>
        )}
      </Dialog>

      <Dialog
        open={deletion !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setDeletion(null);
        }}
        title="确认删除"
        description={`删除「${deletion?.title ?? ""}」？删除分卷不会删除其中章节。`}
      >
        <Alert>{error}</Alert>
        <div className="dialog-actions">
          <Button onClick={() => setDeletion(null)}>取消</Button>
          <Button
            variant="danger"
            disabled={busy}
            onClick={() => {
              const item = deletion;
              if (item)
                void mutate(async () => {
                  if (editor.current?.dirty && selected?.id === item.id && !editor.current.stash())
                    throw new Error("请先保存草稿。");
                  await remove(item.path);
                  if (selected?.id === item.id) setSelected(null);
                  setDeletion(null);
                });
            }}
          >
            删除
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={batchDeleteOpen}
        onOpenChange={(open) => {
          if (!open && !busy) setBatchDeleteOpen(false);
        }}
        title="批量删除设定"
        description={`确定删除选中的 ${selectedEntryIds.length} 条设定吗？此操作无法撤销。`}
      >
        <Alert>{error}</Alert>
        <div className="dialog-actions">
          <Button disabled={busy} onClick={() => setBatchDeleteOpen(false)}>
            取消
          </Button>
          <Button variant="danger" disabled={busy} onClick={() => void applyBatchDelete()}>
            确认删除
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={pendingNavigation !== null}
        onOpenChange={(open) => {
          if (!open) setPendingNavigation(null);
        }}
        title="还有未保存的改动"
        description="可以保存到作品，或保留本地草稿后切换。"
      >
        <div className="dialog-actions navigation-confirm-actions">
          <Button
            variant="primary"
            onClick={() => {
              void editor.current?.save().then((ok) => {
                if (ok) {
                  pendingNavigation?.();
                  setPendingNavigation(null);
                }
              });
            }}
          >
            保存并切换
          </Button>
          <Button
            onClick={() => {
              if (editor.current?.stash()) {
                pendingNavigation?.();
                setPendingNavigation(null);
              }
            }}
          >
            暂存本地并切换
          </Button>
          <Button variant="ghost" onClick={() => setPendingNavigation(null)}>
            继续编辑
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
