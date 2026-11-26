import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { z } from "zod";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  Eye,
  FileClock,
  FilePlus2,
  FileText,
  Folder,
  FolderInput,
  FolderPlus,
  Globe,
  GripVertical,
  LibraryBig,
  ListTree,
  MessageCircle,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Settings2,
  Sparkles,
  Trash2,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import {
  Alert,
  Button,
  Dialog,
  DirectoryToolbar,
  EmptyState,
  Input,
  Menu,
  MenuItem,
  MenuSeparator,
  MenuSub,
} from "../components/ui";
import { ApiError, errorText, remove, request, upload } from "../lib/api";
import { useEvents } from "../lib/events";
import {
  bookSchema,
  chapterSchema,
  entryFolderSchema,
  entrySchema,
  pendingSchema,
  searchSchema,
  trashSchema,
  volumeSchema,
  type Book,
  type Chapter,
  type Entry,
  type EntryFolder,
  type EntryKind,
  type Volume,
} from "../lib/schema";
import { Editor, type DocumentValue, type EditorHandle } from "./Editor";
import { PendingPanel } from "./PendingPanel";
import { ChatPanel } from "./ChatPanel";
import { RevisionDialog } from "./RevisionDialog";
import "./workspace.css";

export type AppRoute =
  | "shelf"
  | "chapters"
  | "characters"
  | "world"
  | "outline"
  | "notes"
  | "discussion"
  | "pending"
  | "trash";
type BookRoute = Exclude<AppRoute, "shelf">;
type EntryRoute = "characters" | "world" | "outline" | "notes";
type EntrySortMode = "order" | "updated" | "title";
type PendingSortMode = "newest" | "oldest";
type Selection = { kind: "chapter" | "entry"; id: string };
type TrashTarget = { kind: "chapter" | "entry"; id: string; title: string };
type GroupForm = {
  target: "volume" | "folder";
  id?: string;
  entryKind?: EntryKind;
  title: string;
  description: string;
};
type RenameForm = {
  target: "chapter" | "entry";
  id: string;
  title: string;
};
type FolderPrompt = { entryId: string; title: string };
type WorkspaceData = {
  volumes: Volume[];
  chapters: Chapter[];
  entries: Entry[];
  folders: EntryFolder[];
  changes: z.infer<typeof pendingSchema>[];
  trashChapters: Chapter[];
  trashEntries: Entry[];
};

const emptyData: WorkspaceData = {
  volumes: [],
  chapters: [],
  entries: [],
  folders: [],
  changes: [],
  trashChapters: [],
  trashEntries: [],
};

const SIDEBAR_WIDTH_KEY = "inkwell:sidebar-width";
const DEFAULT_SIDEBAR_WIDTH = 240;
const MIN_SIDEBAR_WIDTH = 200;
const MAX_SIDEBAR_WIDTH = 420;
const COMPANION_WIDTH_KEY = "inkwell:companion-width";
const DEFAULT_COMPANION_WIDTH = 380;
const MIN_COMPANION_WIDTH = 300;
const MAX_COMPANION_WIDTH = 560;

function clampSidebarWidth(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_SIDEBAR_WIDTH;
  return Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, Math.round(value)));
}

function clampCompanionWidth(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_COMPANION_WIDTH;
  return Math.min(MAX_COMPANION_WIDTH, Math.max(MIN_COMPANION_WIDTH, Math.round(value)));
}

const entryRouteConfig: Record<
  EntryRoute,
  {
    kind: EntryKind;
    label: string;
    itemLabel: string;
    newTitle: string;
    searchLabel: string;
    searchPlaceholder: string;
    emptyTitle: string;
  }
> = {
  characters: {
    kind: "character",
    label: "角色",
    itemLabel: "人物",
    newTitle: "新人物",
    searchLabel: "筛选角色",
    searchPlaceholder: "筛选角色…",
    emptyTitle: "暂无角色",
  },
  world: {
    kind: "world",
    label: "背景设定",
    itemLabel: "条目",
    newTitle: "新条目",
    searchLabel: "筛选背景设定",
    searchPlaceholder: "筛选背景设定…",
    emptyTitle: "暂无背景设定",
  },
  outline: {
    kind: "outline",
    label: "提纲",
    itemLabel: "提纲",
    newTitle: "新提纲",
    searchLabel: "筛选提纲",
    searchPlaceholder: "筛选提纲…",
    emptyTitle: "暂无提纲",
  },
  notes: {
    kind: "note",
    label: "笔记",
    itemLabel: "笔记",
    newTitle: "新笔记",
    searchLabel: "筛选笔记",
    searchPlaceholder: "筛选笔记…",
    emptyTitle: "暂无笔记",
  },
};

const entryKinds: readonly { value: EntryKind; label: string; route: EntryRoute }[] = [
  { value: "character", label: "角色", route: "characters" },
  { value: "world", label: "背景设定", route: "world" },
  { value: "outline", label: "提纲", route: "outline" },
  { value: "note", label: "笔记", route: "notes" },
];

function isEntryRoute(route: AppRoute): route is EntryRoute {
  return route === "characters" || route === "world" || route === "outline" || route === "notes";
}

function routeForEntryKind(kind: EntryKind): EntryRoute {
  if (kind === "character") return "characters";
  if (kind === "world") return "world";
  if (kind === "outline") return "outline";
  return "notes";
}

function entryKindLabel(kind: EntryKind): string {
  return entryRouteConfig[routeForEntryKind(kind)].label;
}

function docIcon(kind: "chapter" | EntryKind, size = 12): ReactNode {
  if (kind === "chapter") return <BookOpen size={size} />;
  if (kind === "character") return <UserRound size={size} />;
  if (kind === "world") return <Globe size={size} />;
  if (kind === "outline") return <ListTree size={size} />;
  return <FileText size={size} />;
}

function formatRelativeTime(iso: string): string {
  if (!iso) return "";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return "";
  const diffMs = Date.now() - ts;
  if (diffMs < 60_000) return "刚刚";
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} 个月前`;
  return new Date(ts).toLocaleDateString("zh-CN");
}

function sortEntriesList(items: Entry[], mode: EntrySortMode): Entry[] {
  const list = [...items];
  if (mode === "updated") {
    list.sort((a, b) => (b.updated_at || b.created_at).localeCompare(a.updated_at || a.created_at));
  } else if (mode === "title") {
    list.sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
  } else {
    list.sort((a, b) => a.sort_order - b.sort_order);
  }
  return list;
}

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
  const [autoFocusTarget, setAutoFocusTarget] = useState<Selection | null>(null);
  const [openTabs, setOpenTabs] = useState<Selection[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);

  const [chapterFilter, setChapterFilter] = useState("");
  const [entryFilter, setEntryFilter] = useState("");
  const [pendingFilter, setPendingFilter] = useState("");
  const [trashFilter, setTrashFilter] = useState("");
  const [entrySort, setEntrySort] = useState<EntrySortMode>("order");
  const [pendingSort, setPendingSort] = useState<PendingSortMode>("newest");
  const [selectedTrash, setSelectedTrash] = useState<{
    kind: "chapter" | "entry";
    id: string;
  } | null>(null);

  const [importing, setImporting] = useState(false);
  const importInputRef = useRef<HTMLInputElement | null>(null);
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
  const [companionWidth, setCompanionWidth] = useState<number>(() => {
    try {
      const saved = localStorage.getItem(COMPANION_WIDTH_KEY);
      return saved ? clampCompanionWidth(Number(saved)) : DEFAULT_COMPANION_WIDTH;
    } catch {
      return DEFAULT_COMPANION_WIDTH;
    }
  });
  const [resizingCompanion, setResizingCompanion] = useState(false);
  const [sessionSidebar, setSessionSidebar] = useState<HTMLDivElement | null>(null);
  const [focusPending, setFocusPending] = useState<string | null>(null);

  const [groupForm, setGroupForm] = useState<GroupForm | null>(null);
  const [renameForm, setRenameForm] = useState<RenameForm | null>(null);
  const [folderPrompt, setFolderPrompt] = useState<FolderPrompt | null>(null);
  const [deletion, setDeletion] = useState<{
    path: string;
    title: string;
    id: string;
    kind: "volume" | "folder" | "chapter" | "entry";
  } | null>(null);
  const [trashConfirm, setTrashConfirm] = useState<TrashTarget | "empty" | null>(null);

  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
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
    setAutoFocusTarget(null);
    setOpenTabs([]);
    setHistoryOpen(false);
    setChapterFilter("");
    setEntryFilter("");
    setPendingFilter("");
    setTrashFilter("");
    setSelectedTrash(null);
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
        const [volumes, chapters, entries, folders, changes, trash] = await Promise.all([
          request(`/books/${currentBookId}/volumes`, z.array(volumeSchema)),
          request(`/books/${currentBookId}/chapters`, z.array(chapterSchema)),
          request(`/books/${currentBookId}/entries`, z.array(entrySchema)),
          request(`/books/${currentBookId}/folders`, z.array(entryFolderSchema)),
          request(`/books/${currentBookId}/pending-changes?status=pending`, z.array(pendingSchema)),
          request(`/books/${currentBookId}/trash`, trashSchema),
        ]);
        if (disposed) return;
        volumes.sort((a, b) => a.sort_order - b.sort_order);
        chapters.sort((a, b) => a.sort_order - b.sort_order);
        entries.sort((a, b) => a.sort_order - b.sort_order);
        folders.sort((a, b) => a.sort_order - b.sort_order);
        setData({
          volumes,
          chapters,
          entries,
          folders,
          changes,
          trashChapters: trash.chapters,
          trashEntries: trash.entries,
        });
        setLoadedBookId(currentBookId);
        setOpenTabs((current) =>
          current.filter((tab) =>
            tab.kind === "chapter"
              ? chapters.some((c) => c.id === tab.id)
              : entries.some((e) => e.id === tab.id),
          ),
        );
        if (!initialized) {
          initialized = true;
          const activeRoute = routeRef.current;
          if (isEntryRoute(activeRoute)) {
            const targetKind = entryRouteConfig[activeRoute].kind;
            const firstEntry = entries.find((item) => item.kind === targetKind);
            if (firstEntry) {
              const initial: Selection = { kind: "entry", id: firstEntry.id };
              setSelected((cur) => cur ?? initial);
              setOpenTabs((cur) => (cur.length > 0 ? cur : [initial]));
            }
          } else {
            let lastId: string | null = null;
            try {
              lastId = localStorage.getItem(`inkwell:last-chapter:${currentBookId}`);
            } catch {
              /* Optional preference. */
            }
            const first =
              chapters.find((item) => item.id === lastId) ??
              chapters.find((item) => item.volume_id === null) ??
              volumes.flatMap((volume) =>
                chapters.filter((item) => item.volume_id === volume.id),
              )[0] ??
              chapters[0];
            if (first) {
              const initial: Selection = { kind: "chapter", id: first.id };
              setSelected((cur) => cur ?? initial);
              setOpenTabs((cur) => (cur.length > 0 ? cur : [initial]));
            }
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

  function targetRouteFor(selection: Selection): BookRoute {
    if (selection.kind === "chapter") return "chapters";
    const found = data.entries.find((item) => item.id === selection.id);
    return found ? routeForEntryKind(found.kind) : "notes";
  }

  function choose(selection: Selection, explicitRoute?: BookRoute) {
    const targetRoute = explicitRoute ?? targetRouteFor(selection);
    setOpenTabs((prev) =>
      prev.some((tab) => tab.kind === selection.kind && tab.id === selection.id)
        ? prev
        : [...prev, selection],
    );
    if (selected?.id === selection.id && selected.kind === selection.kind) {
      onRouteChange(targetRoute);
      return;
    }
    navigate(() => {
      setAutoFocusTarget(null);
      setSelected(selection);
      onRouteChange(targetRoute);
    });
  }

  function closeTab(tab: Selection) {
    const isCurrent = selected?.kind === tab.kind && selected.id === tab.id;
    if (!isCurrent) {
      setOpenTabs((prev) => prev.filter((item) => !(item.kind === tab.kind && item.id === tab.id)));
      return;
    }
    navigate(() => {
      const idx = openTabs.findIndex((item) => item.kind === tab.kind && item.id === tab.id);
      const remaining = openTabs.filter((item) => !(item.kind === tab.kind && item.id === tab.id));
      setOpenTabs(remaining);
      const next = remaining[idx] ?? remaining[idx - 1] ?? null;
      setAutoFocusTarget(null);
      setSelected(next);
      if (next) {
        onRouteChange(targetRouteFor(next));
      }
    });
  }

  function downloadBook(id: string, format: "zip" | "md" | "txt") {
    const link = document.createElement("a");
    link.href = `/api/books/${id}/export?format=${format}`;
    link.download = "";
    link.click();
  }

  async function importZipBook(file: File | undefined) {
    if (!file) return;
    setImporting(true);
    setError("");
    try {
      const imported = await upload("/books/import", bookSchema, file, "POST", "application/zip");
      onRefreshBooks();
      onOpenBook(imported);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setImporting(false);
    }
  }

  function switchRoute(next: AppRoute) {
    if (next === "shelf") {
      navigate(() => onRouteChange("shelf"));
      return;
    }
    if (!bookId) return;
    setSidebarCollapsed(false);
    if (next === "chapters") {
      if (selected?.kind === "chapter") {
        onRouteChange("chapters");
        return;
      }
      const firstChapter =
        data.chapters.find((c) => c.volume_id === null) ??
        data.volumes.flatMap((v) => data.chapters.filter((c) => c.volume_id === v.id))[0] ??
        data.chapters[0];
      if (firstChapter) choose({ kind: "chapter", id: firstChapter.id }, "chapters");
      else
        navigate(() => {
          setAutoFocusTarget(null);
          setSelected(null);
          onRouteChange("chapters");
        });
      return;
    }
    if (isEntryRoute(next)) {
      const targetKind = entryRouteConfig[next].kind;
      const currentEntry =
        selected?.kind === "entry" ? data.entries.find((e) => e.id === selected.id) : undefined;
      if (currentEntry && currentEntry.kind === targetKind) {
        onRouteChange(next);
        return;
      }
      const firstEntry =
        data.entries.find((item) => item.kind === targetKind && !item.category.trim()) ??
        data.entries.find((item) => item.kind === targetKind);
      if (firstEntry) choose({ kind: "entry", id: firstEntry.id }, next);
      else
        navigate(() => {
          setAutoFocusTarget(null);
          setSelected(null);
          onRouteChange(next);
        });
      return;
    }
    navigate(() => onRouteChange(next));
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

  function createChapterDirect(volumeId: string | null = null) {
    if (!bookId || busy) return;
    navigate(() => {
      void (async () => {
        const siblings = data.chapters.filter((item) => item.volume_id === volumeId);
        const sortOrder =
          volumeId === null
            ? siblings.length > 0
              ? Math.min(...siblings.map((c) => c.sort_order)) - 1
              : 0
            : siblings.length > 0
              ? Math.max(...siblings.map((c) => c.sort_order)) + 1
              : 0;
        setBusy(true);
        setError("");
        try {
          const created = await request(`/books/${bookId}/chapters`, chapterSchema, {
            method: "POST",
            body: { title: "新章节", volume_id: volumeId, sort_order: sortOrder },
          });
          if (volumeId) {
            setCollapsedGroups((cur) => ({ ...cur, [`volume:${volumeId}`]: false }));
          }
          setData((cur) => ({
            ...cur,
            chapters: [...cur.chapters, created].sort((a, b) => a.sort_order - b.sort_order),
          }));
          const nextSel: Selection = { kind: "chapter", id: created.id };
          setOpenTabs((prev) =>
            prev.some((tab) => tab.kind === nextSel.kind && tab.id === nextSel.id)
              ? prev
              : [...prev, nextSel],
          );
          setSelected(nextSel);
          setAutoFocusTarget(nextSel);
          onRouteChange("chapters");
          refresh();
        } catch (failure) {
          setError(errorText(failure));
        } finally {
          setBusy(false);
        }
      })();
    });
  }

  function createEntryDirect(kind: EntryKind, category = "") {
    if (!bookId || busy) return;
    const cleanCat = category.trim();
    const targetRoute = routeForEntryKind(kind);
    const cfg = entryRouteConfig[targetRoute];
    navigate(() => {
      void (async () => {
        const siblings = data.entries.filter(
          (item) => item.kind === kind && item.category.trim() === cleanCat,
        );
        const sortOrder =
          cleanCat === ""
            ? siblings.length > 0
              ? Math.min(...siblings.map((e) => e.sort_order)) - 1
              : 0
            : siblings.length > 0
              ? Math.max(...siblings.map((e) => e.sort_order)) + 1
              : 0;
        setBusy(true);
        setError("");
        try {
          const created = await request(`/books/${bookId}/entries`, entrySchema, {
            method: "POST",
            body: {
              title: cfg.newTitle,
              kind,
              category: cleanCat,
              visibility: "local",
              sort_order: sortOrder,
            },
          });
          if (cleanCat) {
            setCollapsedGroups((cur) => ({ ...cur, [`folder:${kind}:${cleanCat}`]: false }));
          }
          setData((cur) => ({
            ...cur,
            entries: [...cur.entries, created].sort((a, b) => a.sort_order - b.sort_order),
          }));
          const nextSel: Selection = { kind: "entry", id: created.id };
          setOpenTabs((prev) =>
            prev.some((tab) => tab.kind === nextSel.kind && tab.id === nextSel.id)
              ? prev
              : [...prev, nextSel],
          );
          setSelected(nextSel);
          setAutoFocusTarget(nextSel);
          onRouteChange(targetRoute);
          refresh();
        } catch (failure) {
          setError(errorText(failure));
        } finally {
          setBusy(false);
        }
      })();
    });
  }

  async function submitGroupForm() {
    if (!groupForm || !groupForm.title.trim() || !bookId) return;
    const snapshot = groupForm;
    const ok = await mutate(async () => {
      if (snapshot.target === "volume") {
        const url = snapshot.id ? `/volumes/${snapshot.id}` : `/books/${bookId}/volumes`;
        await request(url, volumeSchema, {
          method: snapshot.id ? "PATCH" : "POST",
          body: {
            title: snapshot.title.trim(),
            description: snapshot.description.trim(),
            ...(snapshot.id ? {} : { sort_order: data.volumes.length }),
          },
        });
      } else {
        const kind = snapshot.entryKind ?? "note";
        const url = snapshot.id ? `/folders/${snapshot.id}` : `/books/${bookId}/folders`;
        await request(url, entryFolderSchema, {
          method: snapshot.id ? "PATCH" : "POST",
          body: snapshot.id
            ? {
                title: snapshot.title.trim(),
                description: snapshot.description.trim(),
              }
            : {
                kind,
                title: snapshot.title.trim(),
                description: snapshot.description.trim(),
                sort_order: data.folders.filter((f) => f.kind === kind).length,
              },
        });
      }
    });
    if (ok) setGroupForm(null);
  }

  async function submitRenameForm() {
    if (!renameForm || !renameForm.title.trim()) return;
    const snapshot = renameForm;
    const ok = await mutate(async () => {
      if (snapshot.target === "chapter") {
        await request(`/chapters/${snapshot.id}`, chapterSchema, {
          method: "PATCH",
          body: { title: snapshot.title.trim() },
        });
      } else {
        await request(`/entries/${snapshot.id}`, entrySchema, {
          method: "PATCH",
          body: { title: snapshot.title.trim() },
        });
      }
    });
    if (ok) setRenameForm(null);
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
          request(`/volumes/${item.id}`, volumeSchema, {
            method: "PATCH",
            body: { sort_order: order },
          }),
        ),
      ),
    );
  }

  async function reorderFolders(kind: EntryKind, id: string, direction: number) {
    const items = data.folders.filter((f) => f.kind === kind);
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
          request(`/folders/${item.id}`, entryFolderSchema, {
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

  async function restoreTrashItem(target: TrashTarget) {
    if (!bookId) return;
    const ok = await mutate(() =>
      request(`/books/${bookId}/trash/restore`, z.union([chapterSchema, entrySchema]), {
        method: "POST",
        body: { target_type: target.kind, target_id: target.id },
      }),
    );
    if (ok && selectedTrash?.kind === target.kind && selectedTrash.id === target.id) {
      setSelectedTrash(null);
    }
  }

  async function confirmPurgeTrash() {
    if (!bookId || !trashConfirm) return;
    const ok = await mutate(async () => {
      if (trashConfirm === "empty") {
        await remove(`/books/${bookId}/trash`);
        setSelectedTrash(null);
      } else {
        await remove(`/books/${bookId}/trash/${trashConfirm.kind}/${trashConfirm.id}`);
        if (selectedTrash?.kind === trashConfirm.kind && selectedTrash.id === trashConfirm.id) {
          setSelectedTrash(null);
        }
      }
    });
    if (ok) setTrashConfirm(null);
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

  function renderChapterRow(item: Chapter) {
    const dropKey = `chapter:${item.id}`;
    const isSelected = selected?.kind === "chapter" && selected.id === item.id;
    const relTime = formatRelativeTime(item.updated_at || item.created_at);
    return (
      <div
        key={item.id}
        className={`directory-row ${isSelected ? "is-selected" : ""} ${dropTarget === dropKey ? "is-drop-target" : ""}`}
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
        <button
          type="button"
          className="directory-row-trigger"
          onClick={() => choose({ kind: "chapter", id: item.id }, "chapters")}
        >
          <span className="directory-row-title">{item.title}</span>
          <span className="directory-row-meta">
            {item.word_count.toLocaleString("zh-CN")} 字{relTime ? ` · ${relTime}` : ""}
          </span>
        </button>
        <div
          className="directory-actions"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
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
              onSelect={() => setRenameForm({ target: "chapter", id: item.id, title: item.title })}
            >
              <Pencil size={14} />
              重命名
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              danger
              onSelect={() =>
                setDeletion({
                  path: `/chapters/${item.id}`,
                  title: item.title,
                  id: item.id,
                  kind: "chapter",
                })
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
    const dropKey = `entry:${item.id}`;
    const isSelected = selected?.kind === "entry" && selected.id === item.id;
    const foldersInKind = data.folders.filter((f) => f.kind === item.kind);
    const relTime = formatRelativeTime(item.updated_at || item.created_at);
    return (
      <div
        className={`directory-row ${isSelected ? "is-selected" : ""} ${dropTarget === dropKey ? "is-drop-target" : ""}`}
        key={item.id}
        draggable={entrySort === "order"}
        onDragStart={(event) => {
          if (entrySort !== "order") return;
          event.dataTransfer.effectAllowed = "move";
          setDragItem({ kind: "entry", id: item.id });
        }}
        onDragOver={(event) => {
          if (entrySort === "order" && dragItem?.kind === "entry" && dragItem.id !== item.id) {
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
          if (entrySort === "order" && dragItem?.kind === "entry") {
            void dropEntryOnEntry(dragItem.id, item);
          }
          setDragItem(null);
        }}
        onDragEnd={() => {
          setDragItem(null);
          setDropTarget(null);
        }}
      >
        <span
          className={`directory-drag-handle ${entrySort !== "order" ? "is-disabled" : ""}`}
          aria-hidden="true"
        >
          <GripVertical size={13} />
        </span>
        <button
          type="button"
          className="directory-row-trigger"
          onClick={() => choose({ kind: "entry", id: item.id })}
        >
          <span className="directory-row-title">{item.title}</span>
          {relTime && <span className="directory-row-meta">{relTime}</span>}
        </button>
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
        <div
          className="directory-actions"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          <Menu label={`${item.title}的操作`} iconOnly>
            <MenuSub label="移动到…" icon={<FolderInput size={14} />}>
              {foldersInKind.map((folder) => (
                <MenuItem
                  key={folder.id}
                  onSelect={() => void moveEntry(item, item.kind, folder.title)}
                >
                  <Check
                    size={13}
                    className={item.category.trim() === folder.title ? "opacity-100" : "opacity-0"}
                  />
                  {folder.title}
                </MenuItem>
              ))}
              {item.category.trim() && (
                <MenuItem onSelect={() => void moveEntry(item, item.kind, "")}>
                  <Folder size={13} />
                  移出文件夹
                </MenuItem>
              )}
              <MenuSeparator />
              <MenuItem onSelect={() => setFolderPrompt({ entryId: item.id, title: "" })}>
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
              onSelect={() => setRenameForm({ target: "entry", id: item.id, title: item.title })}
            >
              <Pencil size={14} />
              重命名
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              danger
              onSelect={() =>
                setDeletion({
                  path: `/entries/${item.id}`,
                  title: item.title,
                  id: item.id,
                  kind: "entry",
                })
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

  const visiblePending = useMemo(() => {
    const q = pendingFilter.trim().toLowerCase();
    const filtered = data.changes.filter(
      (change) =>
        !q ||
        change.title.toLowerCase().includes(q) ||
        change.after_content.toLowerCase().includes(q),
    );
    filtered.sort((a, b) => {
      const ta = a.created_at ?? "";
      const tb = b.created_at ?? "";
      return pendingSort === "newest" ? tb.localeCompare(ta) : ta.localeCompare(tb);
    });
    return filtered;
  }, [data.changes, pendingFilter, pendingSort]);

  const currentPending =
    visiblePending.find((change) => change.id === focusPending) ?? visiblePending[0];

  const trashItems = useMemo(() => {
    const combined = [
      ...data.trashChapters.map((c) => ({
        kind: "chapter" as const,
        id: c.id,
        title: c.title,
        content: c.content,
        kindLabel: "章节",
        deletedAt: c.deleted_at ?? c.updated_at,
      })),
      ...data.trashEntries.map((e) => ({
        kind: "entry" as const,
        id: e.id,
        title: e.title,
        content: e.content,
        kindLabel: entryKindLabel(e.kind),
        deletedAt: e.deleted_at ?? e.updated_at,
      })),
    ];
    combined.sort((a, b) => b.deletedAt.localeCompare(a.deletedAt));
    const q = trashFilter.trim().toLowerCase();
    return q
      ? combined.filter(
          (item) => item.title.toLowerCase().includes(q) || item.content.toLowerCase().includes(q),
        )
      : combined;
  }, [data.trashChapters, data.trashEntries, trashFilter]);

  const activeTrashItem =
    trashItems.find((item) => item.kind === selectedTrash?.kind && item.id === selectedTrash.id) ??
    trashItems[0] ??
    null;

  const trashTotalCount = data.trashChapters.length + data.trashEntries.length;
  const editing = route === "chapters" || isEntryRoute(route);
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

  const updateCompanionWidth = useCallback((nextWidth: number) => {
    const clamped = clampCompanionWidth(nextWidth);
    setCompanionWidth(clamped);
    try {
      localStorage.setItem(COMPANION_WIDTH_KEY, String(clamped));
    } catch {
      /* Optional preference. */
    }
    return clamped;
  }, []);

  const startCompanionResize = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!(editing && companionOpen)) return;
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = companionWidth;
      let latestWidth = startWidth;
      setResizingCompanion(true);

      const onMove = (moveEvent: PointerEvent) => {
        latestWidth = clampCompanionWidth(startWidth + (startX - moveEvent.clientX));
        setCompanionWidth(latestWidth);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        setResizingCompanion(false);
        updateCompanionWidth(latestWidth);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [editing, companionOpen, companionWidth, updateCompanionWidth],
  );

  useEffect(() => {
    function checkViewport() {
      if (route === "shelf") return;
      if (window.innerWidth < 820 || (editing && companionOpen && window.innerWidth < 1140)) {
        setSidebarCollapsed(true);
      }
    }
    checkViewport();
    window.addEventListener("resize", checkViewport);
    return () => window.removeEventListener("resize", checkViewport);
  }, [route, editing, companionOpen]);

  function renderEntryDirectory(activeRoute: EntryRoute) {
    const cfg = entryRouteConfig[activeRoute];
    const kind = cfg.kind;
    const q = entryFilter.trim().toLowerCase();
    const kindFolders = data.folders.filter((f) => f.kind === kind);
    const kindEntries = data.entries.filter((e) => e.kind === kind);

    const matchesQuery = (item: Entry) =>
      !q || item.title.toLowerCase().includes(q) || item.category.toLowerCase().includes(q);

    const uncategorized = sortEntriesList(
      kindEntries.filter((item) => !item.category.trim() && matchesQuery(item)),
      entrySort,
    );

    const folderBuckets = kindFolders.map((folder) => {
      const folderMatches = !q || folder.title.toLowerCase().includes(q);
      const items = sortEntriesList(
        kindEntries.filter(
          (item) => item.category.trim() === folder.title && (folderMatches || matchesQuery(item)),
        ),
        entrySort,
      );
      return { folder, items, visible: !q || folderMatches || items.length > 0 };
    });

    const uncategorizedDropKey = `uncategorized:${kind}`;

    return (
      <>
        <DirectoryToolbar
          searchValue={entryFilter}
          onSearchChange={setEntryFilter}
          searchLabel={cfg.searchLabel}
          searchPlaceholder={cfg.searchPlaceholder}
          create={
            <Menu
              label={`新建${cfg.label}`}
              iconOnly
              icon={<Plus size={15} />}
              triggerClassName="icon-control"
            >
              <MenuItem onSelect={() => createEntryDirect(kind, "")}>
                <FilePlus2 size={14} />
                新建{cfg.itemLabel}
              </MenuItem>
              <MenuItem
                onSelect={() =>
                  setGroupForm({
                    target: "folder",
                    entryKind: kind,
                    title: "",
                    description: "",
                  })
                }
              >
                <FolderPlus size={14} />
                新建文件夹
              </MenuItem>
            </Menu>
          }
          more={
            <Menu label={`${cfg.label}列表操作`} iconOnly triggerClassName="icon-control">
              <MenuItem onSelect={() => setEntrySort("order")}>
                <Check size={14} className={entrySort === "order" ? "opacity-100" : "opacity-0"} />
                按手动排序
              </MenuItem>
              <MenuItem onSelect={() => setEntrySort("updated")}>
                <Check
                  size={14}
                  className={entrySort === "updated" ? "opacity-100" : "opacity-0"}
                />
                按最近更新
              </MenuItem>
              <MenuItem onSelect={() => setEntrySort("title")}>
                <Check size={14} className={entrySort === "title" ? "opacity-100" : "opacity-0"} />
                按名称排序
              </MenuItem>
              {kindFolders.length > 0 && (
                <>
                  <MenuSeparator />
                  <MenuItem
                    onSelect={() => {
                      setCollapsedGroups((cur) => {
                        const next = { ...cur };
                        for (const f of kindFolders) {
                          delete next[`folder:${kind}:${f.title}`];
                        }
                        return next;
                      });
                    }}
                  >
                    <ChevronDown size={14} />
                    展开全部文件夹
                  </MenuItem>
                  <MenuItem
                    onSelect={() => {
                      setCollapsedGroups((cur) => {
                        const next = { ...cur };
                        for (const f of kindFolders) {
                          next[`folder:${kind}:${f.title}`] = true;
                        }
                        return next;
                      });
                    }}
                  >
                    <ChevronRight size={14} />
                    折叠全部文件夹
                  </MenuItem>
                </>
              )}
            </Menu>
          }
        />

        <div
          className={`directory-uncategorized ${dropTarget === uncategorizedDropKey ? "is-drop-target" : ""}`}
          onDragOver={(event) => {
            if (entrySort === "order" && dragItem?.kind === "entry") {
              event.preventDefault();
              setDropTarget(uncategorizedDropKey);
            }
          }}
          onDragLeave={() => {
            if (dropTarget === uncategorizedDropKey) setDropTarget(null);
          }}
          onDrop={(event) => {
            event.preventDefault();
            setDropTarget(null);
            if (entrySort === "order" && dragItem?.kind === "entry") {
              const dragged = data.entries.find((e) => e.id === dragItem.id);
              if (dragged) void moveEntry(dragged, kind, "");
            }
            setDragItem(null);
          }}
        >
          {uncategorized.map((item) => renderEntryRow(item))}
        </div>

        {folderBuckets.map(({ folder, items, visible }, index) => {
          if (!visible) return null;
          const folderKey = `folder:${kind}:${folder.title}`;
          const collapsed = q ? false : Boolean(collapsedGroups[folderKey]);
          return (
            <section className="directory-group" key={folder.id}>
              <div
                className={`directory-group-header ${dropTarget === folderKey ? "is-drop-target" : ""}`}
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
                    if (dragged) void moveEntry(dragged, kind, folder.title);
                  }
                  setDragItem(null);
                }}
              >
                <button
                  type="button"
                  className="directory-group-toggle"
                  aria-expanded={!collapsed}
                  onClick={() => setCollapsedGroups((cur) => ({ ...cur, [folderKey]: !collapsed }))}
                >
                  {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                  <div className="directory-group-heading">
                    <span className="directory-group-name">{folder.title}</span>
                    {folder.description && (
                      <span className="directory-group-subtitle">{folder.description}</span>
                    )}
                  </div>
                </button>
                <span className="directory-group-count">{items.length}</span>
                <div className="directory-actions">
                  <Button
                    variant="ghost"
                    className="icon-control"
                    title={`在 ${folder.title} 新建${cfg.itemLabel}`}
                    aria-label={`在 ${folder.title} 新建${cfg.itemLabel}`}
                    onClick={() => createEntryDirect(kind, folder.title)}
                  >
                    <Plus size={14} />
                  </Button>
                  <Menu label={`${folder.title}的操作`} iconOnly>
                    <MenuItem
                      onSelect={() =>
                        setGroupForm({
                          target: "folder",
                          id: folder.id,
                          entryKind: kind,
                          title: folder.title,
                          description: folder.description,
                        })
                      }
                    >
                      <Pencil size={14} />
                      编辑名称与副标题
                    </MenuItem>
                    <MenuItem
                      disabled={busy || index === 0}
                      onSelect={() => void reorderFolders(kind, folder.id, -1)}
                    >
                      <ArrowUp size={14} />
                      上移
                    </MenuItem>
                    <MenuItem
                      disabled={busy || index === kindFolders.length - 1}
                      onSelect={() => void reorderFolders(kind, folder.id, 1)}
                    >
                      <ArrowDown size={14} />
                      下移
                    </MenuItem>
                    <MenuSeparator />
                    <MenuItem
                      danger
                      onSelect={() =>
                        setDeletion({
                          path: `/folders/${folder.id}`,
                          title: folder.title,
                          id: folder.id,
                          kind: "folder",
                        })
                      }
                    >
                      <Trash2 size={14} />
                      删除文件夹
                    </MenuItem>
                  </Menu>
                </div>
              </div>
              {!collapsed && (
                <div className="directory-group-items">
                  {items.length > 0 ? (
                    items.map((item) => renderEntryRow(item))
                  ) : (
                    <p className="directory-group-empty">此文件夹暂无条目</p>
                  )}
                </div>
              )}
            </section>
          );
        })}

        {loaded && kindEntries.length === 0 && kindFolders.length === 0 && (
          <EmptyState title={cfg.emptyTitle}>
            <Button onClick={() => createEntryDirect(kind, "")}>
              <Plus size={14} />
              新建{cfg.itemLabel}
            </Button>
          </EmptyState>
        )}
      </>
    );
  }

  return (
    <div
      className={`app-shell ${sidebarClosed ? "sidebar-collapsed" : ""} ${resizing || resizingCompanion ? "is-resizing" : ""}`}
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
          <LibraryBig size={20} />
        </Button>

        <div className="activity-primary">
          <Button
            variant="ghost"
            className={`activity-button ${route === "chapters" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `写作 · ${book.title}` : "请先新建作品"}
            aria-label="写作"
            aria-pressed={route === "chapters"}
            onClick={() => switchRoute("chapters")}
          >
            <BookOpen size={20} />
          </Button>
          <Button
            variant="ghost"
            className={`activity-button ${route === "characters" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `角色 · ${book.title}` : "请先新建作品"}
            aria-label="角色"
            aria-pressed={route === "characters"}
            onClick={() => switchRoute("characters")}
          >
            <UserRound size={20} />
          </Button>
          <Button
            variant="ghost"
            className={`activity-button ${route === "world" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `背景设定 · ${book.title}` : "请先新建作品"}
            aria-label="背景设定"
            aria-pressed={route === "world"}
            onClick={() => switchRoute("world")}
          >
            <Globe size={20} />
          </Button>
          <Button
            variant="ghost"
            className={`activity-button ${route === "outline" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `提纲 · ${book.title}` : "请先新建作品"}
            aria-label="提纲"
            aria-pressed={route === "outline"}
            onClick={() => switchRoute("outline")}
          >
            <ListTree size={20} />
          </Button>
          <Button
            variant="ghost"
            className={`activity-button ${route === "notes" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `笔记 · ${book.title}` : "请先新建作品"}
            aria-label="笔记"
            aria-pressed={route === "notes"}
            onClick={() => switchRoute("notes")}
          >
            <FileText size={20} />
          </Button>

          <div className="activity-divider" aria-hidden="true" />

          <Button
            variant="ghost"
            className={`activity-button ${route === "discussion" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `讨论 · ${book.title}` : "请先新建作品"}
            aria-label="讨论"
            aria-pressed={route === "discussion"}
            onClick={() => switchRoute("discussion")}
          >
            <MessageCircle size={20} />
          </Button>
          <Button
            variant="ghost"
            className={`activity-button ${route === "pending" ? "is-active" : ""}`}
            disabled={!book}
            title={book ? `待审变更 · ${book.title}` : "请先新建作品"}
            aria-label="待审变更"
            aria-pressed={route === "pending"}
            onClick={() => switchRoute("pending")}
          >
            <FileClock size={20} />
            {data.changes.length > 0 && (
              <span className="activity-count">
                {data.changes.length > 99 ? "99+" : data.changes.length}
              </span>
            )}
          </Button>
          <Button
            variant="ghost"
            className={`activity-button ${route === "trash" ? "is-active" : ""}`}
            disabled={!book}
            title={
              book
                ? trashTotalCount > 0
                  ? `回收站（${trashTotalCount}）`
                  : "回收站"
                : "请先新建作品"
            }
            aria-label="回收站"
            aria-pressed={route === "trash"}
            onClick={() => switchRoute("trash")}
          >
            <Trash2 size={20} />
          </Button>
          <Button
            variant="ghost"
            className="activity-button"
            disabled={!book}
            title={book ? "全书搜索 · Ctrl+K" : "请先新建作品"}
            aria-label="全书搜索"
            onClick={() => {
              if (!book) return;
              if (route === "shelf") onOpenBook(book, "chapters", true);
              else onSearchOpenChange(true);
            }}
          >
            <Search size={20} />
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
            <Settings2 size={20} />
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
              <DirectoryToolbar
                searchValue={chapterFilter}
                onSearchChange={setChapterFilter}
                searchLabel="筛选章节"
                searchPlaceholder="筛选章节…"
                create={
                  <Menu
                    label="新建章节或分卷"
                    iconOnly
                    icon={<Plus size={15} />}
                    triggerClassName="icon-control"
                  >
                    <MenuItem onSelect={() => createChapterDirect(null)}>
                      <FilePlus2 size={14} />
                      新建章节
                    </MenuItem>
                    <MenuItem
                      onSelect={() =>
                        setGroupForm({ target: "volume", title: "", description: "" })
                      }
                    >
                      <FolderPlus size={14} />
                      新建分卷
                    </MenuItem>
                  </Menu>
                }
                more={
                  <Menu label="章节列表操作" iconOnly triggerClassName="icon-control">
                    {data.volumes.length > 0 && (
                      <>
                        <MenuItem
                          onSelect={() => {
                            setCollapsedGroups((cur) => {
                              const next = { ...cur };
                              for (const v of data.volumes) delete next[`volume:${v.id}`];
                              return next;
                            });
                          }}
                        >
                          <ChevronDown size={14} />
                          展开全部分卷
                        </MenuItem>
                        <MenuItem
                          onSelect={() => {
                            setCollapsedGroups((cur) => {
                              const next = { ...cur };
                              for (const v of data.volumes) next[`volume:${v.id}`] = true;
                              return next;
                            });
                          }}
                        >
                          <ChevronRight size={14} />
                          折叠全部分卷
                        </MenuItem>
                        <MenuSeparator />
                      </>
                    )}
                    {bookId && (
                      <MenuSub label="导出作品" icon={<Download size={14} />}>
                        <MenuItem onSelect={() => downloadBook(bookId, "zip")}>ZIP 完整包</MenuItem>
                        <MenuItem onSelect={() => downloadBook(bookId, "md")}>
                          Markdown 正文
                        </MenuItem>
                        <MenuItem onSelect={() => downloadBook(bookId, "txt")}>TXT 正文</MenuItem>
                      </MenuSub>
                    )}
                  </Menu>
                }
              />

              {(() => {
                const chapterQuery = chapterFilter.trim().toLowerCase();
                const uncategorizedChapters = data.chapters.filter(
                  (item) =>
                    item.volume_id === null &&
                    (!chapterQuery || item.title.toLowerCase().includes(chapterQuery)),
                );
                const uncategorizedDropKey = "volume:none";
                return (
                  <>
                    <div
                      className={`directory-uncategorized ${dropTarget === uncategorizedDropKey ? "is-drop-target" : ""}`}
                      onDragOver={(event) => {
                        if (dragItem?.kind === "chapter") {
                          event.preventDefault();
                          setDropTarget(uncategorizedDropKey);
                        }
                      }}
                      onDragLeave={() => {
                        if (dropTarget === uncategorizedDropKey) setDropTarget(null);
                      }}
                      onDrop={(event) => {
                        event.preventDefault();
                        setDropTarget(null);
                        if (dragItem?.kind === "chapter") {
                          const ch = data.chapters.find((c) => c.id === dragItem.id);
                          if (ch) void moveChapterToVolume(ch, null);
                        }
                        setDragItem(null);
                      }}
                    >
                      {uncategorizedChapters.map((item) => renderChapterRow(item))}
                    </div>

                    {data.volumes.map((volume, index) => {
                      const dropKey = `volume:${volume.id}`;
                      const collapsed = chapterQuery ? false : Boolean(collapsedGroups[dropKey]);
                      const volumeMatches =
                        !chapterQuery || volume.title.toLowerCase().includes(chapterQuery);
                      const volumeChapters = data.chapters.filter(
                        (item) =>
                          item.volume_id === volume.id &&
                          (volumeMatches || item.title.toLowerCase().includes(chapterQuery)),
                      );
                      if (chapterQuery && !volumeMatches && volumeChapters.length === 0) {
                        return null;
                      }
                      return (
                        <section className="directory-group" key={volume.id}>
                          <div
                            className={`directory-group-header ${dropTarget === dropKey ? "is-drop-target" : ""}`}
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
                            <button
                              type="button"
                              className="directory-group-toggle"
                              aria-expanded={!collapsed}
                              onClick={() =>
                                setCollapsedGroups((cur) => ({ ...cur, [dropKey]: !collapsed }))
                              }
                            >
                              {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                              <div className="directory-group-heading">
                                <span className="directory-group-name">{volume.title}</span>
                                {volume.description && (
                                  <span className="directory-group-subtitle">
                                    {volume.description}
                                  </span>
                                )}
                              </div>
                            </button>
                            <span className="directory-group-count">{volumeChapters.length}</span>
                            <div className="directory-actions">
                              <Button
                                variant="ghost"
                                className="icon-control"
                                title={`在 ${volume.title} 新建章节`}
                                aria-label={`在 ${volume.title} 新建章节`}
                                onClick={() => createChapterDirect(volume.id)}
                              >
                                <Plus size={14} />
                              </Button>
                              <Menu label={`${volume.title}的操作`} iconOnly>
                                <MenuItem
                                  onSelect={() =>
                                    setGroupForm({
                                      target: "volume",
                                      id: volume.id,
                                      title: volume.title,
                                      description: volume.description,
                                    })
                                  }
                                >
                                  <Pencil size={14} />
                                  编辑名称与副标题
                                </MenuItem>
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
                                <MenuSeparator />
                                <MenuItem
                                  danger
                                  onSelect={() =>
                                    setDeletion({
                                      path: `/volumes/${volume.id}`,
                                      title: volume.title,
                                      id: volume.id,
                                      kind: "volume",
                                    })
                                  }
                                >
                                  <Trash2 size={14} />
                                  删除
                                </MenuItem>
                              </Menu>
                            </div>
                          </div>
                          {!collapsed && (
                            <div className="directory-group-items">
                              {volumeChapters.length > 0 ? (
                                volumeChapters.map((item) => renderChapterRow(item))
                              ) : (
                                <p className="directory-group-empty">此卷暂无章节</p>
                              )}
                            </div>
                          )}
                        </section>
                      );
                    })}
                  </>
                );
              })()}
              {loaded && data.chapters.length === 0 && data.volumes.length === 0 && (
                <EmptyState title="暂无章节">
                  <Button onClick={() => createChapterDirect(null)}>
                    <Plus size={14} />
                    新建章节
                  </Button>
                </EmptyState>
              )}
            </>
          )}

          {isEntryRoute(route) && renderEntryDirectory(route)}

          <div
            ref={setSessionSidebar}
            hidden={route !== "discussion"}
            className="session-sidebar-target"
          />

          {route === "pending" && (
            <>
              <DirectoryToolbar
                searchValue={pendingFilter}
                onSearchChange={setPendingFilter}
                searchLabel="筛选待审变更"
                searchPlaceholder="筛选待审变更…"
                badge={
                  data.changes.length > 0 ? (
                    <span className="directory-toolbar-badge">{data.changes.length}</span>
                  ) : null
                }
                more={
                  <Menu label="待审变更排序" iconOnly triggerClassName="icon-control">
                    <MenuItem onSelect={() => setPendingSort("newest")}>
                      <Check
                        size={14}
                        className={pendingSort === "newest" ? "opacity-100" : "opacity-0"}
                      />
                      最新优先
                    </MenuItem>
                    <MenuItem onSelect={() => setPendingSort("oldest")}>
                      <Check
                        size={14}
                        className={pendingSort === "oldest" ? "opacity-100" : "opacity-0"}
                      />
                      最旧优先
                    </MenuItem>
                  </Menu>
                }
              />
              {visiblePending.map((change) => (
                <button
                  key={change.id}
                  type="button"
                  className={`pending-directory-item ${currentPending?.id === change.id ? "is-selected" : ""}`}
                  onClick={() => setFocusPending(change.id)}
                >
                  <span className="directory-row-title">{change.title || "正文变更"}</span>
                  <span className="directory-row-meta">
                    {change.action === "create"
                      ? "新增"
                      : change.action === "delete"
                        ? "删除"
                        : "修改"}
                    {" · "}
                    {change.target_type === "chapter" ? "章节" : "设定"}
                    {change.created_at ? ` · ${formatRelativeTime(change.created_at)}` : ""}
                  </span>
                </button>
              ))}
              {visiblePending.length === 0 && <p className="directory-empty">暂无待审变更</p>}
            </>
          )}

          {route === "trash" && (
            <>
              <DirectoryToolbar
                searchValue={trashFilter}
                onSearchChange={setTrashFilter}
                searchLabel="筛选回收站"
                searchPlaceholder="筛选回收站…"
                more={
                  <Menu label="回收站操作" iconOnly triggerClassName="icon-control">
                    <MenuItem
                      danger
                      disabled={busy || trashTotalCount === 0}
                      onSelect={() => setTrashConfirm("empty")}
                    >
                      <Trash2 size={14} />
                      清空回收站
                    </MenuItem>
                  </Menu>
                }
              />
              {trashItems.map((item) => {
                const isSelected =
                  activeTrashItem?.kind === item.kind && activeTrashItem.id === item.id;
                const deletedTime = formatRelativeTime(item.deletedAt);
                return (
                  <div
                    key={`${item.kind}:${item.id}`}
                    className={`directory-row trash-row ${isSelected ? "is-selected" : ""}`}
                  >
                    <button
                      type="button"
                      className="directory-row-trigger"
                      onClick={() => setSelectedTrash({ kind: item.kind, id: item.id })}
                    >
                      <span className="directory-row-title">{item.title || "无标题"}</span>
                      <span className="directory-row-meta">
                        {item.kindLabel}
                        {deletedTime ? ` · 删除于 ${deletedTime}` : ""}
                      </span>
                    </button>
                    <div className="directory-actions">
                      <Button
                        variant="ghost"
                        className="icon-control"
                        title="还原"
                        aria-label={`还原 ${item.title}`}
                        disabled={busy}
                        onClick={() =>
                          void restoreTrashItem({
                            kind: item.kind,
                            id: item.id,
                            title: item.title,
                          })
                        }
                      >
                        <RotateCcw size={14} />
                      </Button>
                    </div>
                  </div>
                );
              })}
              {trashItems.length === 0 && <p className="directory-empty">回收站为空</p>}
            </>
          )}
        </div>
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
              : route === "pending" || route === "trash"
                ? "pending-view"
                : editing && companionOpen
                  ? "with-companion"
                  : ""
        }`}
        style={
          editing && companionOpen
            ? { gridTemplateColumns: `minmax(0, 1fr) ${companionWidth}px` }
            : undefined
        }
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
                    <input
                      ref={importInputRef}
                      type="file"
                      accept=".zip,application/zip"
                      hidden
                      onChange={(event) => {
                        void importZipBook(event.target.files?.[0]);
                        event.target.value = "";
                      }}
                    />
                    <Button disabled={importing} onClick={() => importInputRef.current?.click()}>
                      <Upload size={16} />
                      {importing ? "导入中…" : "导入作品"}
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
                            <p className="book-card-stats">
                              {item.word_count.toLocaleString("zh-CN")} 字 · {item.chapter_count} 章
                            </p>
                            <div className="book-card-footer">
                              <span className="book-card-date">
                                {new Date(item.updated_at).toLocaleDateString("zh-CN")}
                              </span>
                              <div
                                className="book-card-actions"
                                onClick={(event) => event.stopPropagation()}
                                onKeyDown={(event) => event.stopPropagation()}
                              >
                                <Menu
                                  label={`导出 ${item.title}`}
                                  iconOnly
                                  icon={<Download size={14} />}
                                  triggerClassName="book-card-action"
                                >
                                  <MenuItem onSelect={() => downloadBook(item.id, "zip")}>
                                    ZIP 完整包
                                  </MenuItem>
                                  <MenuItem onSelect={() => downloadBook(item.id, "md")}>
                                    Markdown 正文
                                  </MenuItem>
                                  <MenuItem onSelect={() => downloadBook(item.id, "txt")}>
                                    TXT 正文
                                  </MenuItem>
                                </Menu>
                                <Button
                                  variant="ghost"
                                  className="icon-button book-card-action"
                                  title={`编辑 ${item.title}`}
                                  aria-label={`编辑 ${item.title}`}
                                  onClick={() => onEditBook(item)}
                                >
                                  <Pencil size={14} />
                                </Button>
                                <Button
                                  variant="ghost"
                                  className="icon-button book-card-action book-card-action-danger"
                                  title={`删除 ${item.title}`}
                                  aria-label={`删除 ${item.title}`}
                                  onClick={() => onDeleteBook(item)}
                                >
                                  <Trash2 size={14} />
                                </Button>
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
              {openTabs.length > 0 && (
                <div className="doc-tabs-bar" role="tablist" aria-label="已打开文档">
                  {openTabs.map((tab) => {
                    const tabChapter =
                      tab.kind === "chapter"
                        ? data.chapters.find((c) => c.id === tab.id)
                        : undefined;
                    const tabEntry =
                      tab.kind === "entry" ? data.entries.find((e) => e.id === tab.id) : undefined;
                    const tabDoc = tabChapter ?? tabEntry;
                    if (!tabDoc) return null;
                    const isActiveTab = selected?.kind === tab.kind && selected.id === tab.id;
                    return (
                      <div
                        key={`${tab.kind}:${tab.id}`}
                        className={`doc-tab ${isActiveTab ? "is-active" : ""}`}
                      >
                        <button
                          type="button"
                          role="tab"
                          aria-selected={isActiveTab}
                          className="doc-tab-trigger"
                          title={tabDoc.title}
                          onClick={() => choose(tab)}
                        >
                          {docIcon(tab.kind === "chapter" ? "chapter" : (tabEntry?.kind ?? "note"))}
                          <span>{tabDoc.title}</span>
                        </button>
                        <button
                          type="button"
                          className="doc-tab-close"
                          aria-label={`关闭 ${tabDoc.title}`}
                          onClick={() => closeTab(tab)}
                        >
                          <X size={12} />
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
              {documentValue && selected && bookId ? (
                <>
                  <Editor
                    key={`${selected.kind}:${selected.id}`}
                    documentKey={`${bookId}:${selected.kind}:${selected.id}`}
                    value={documentValue}
                    kind={
                      selected.kind === "chapter" ? "章节" : entryKindLabel(entry?.kind ?? "note")
                    }
                    onSave={save}
                    onHistory={() => setHistoryOpen(true)}
                    ref={editor}
                    active={editing}
                    autoFocusTitle={
                      autoFocusTarget?.kind === selected.kind && autoFocusTarget.id === selected.id
                    }
                    toolbarStart={sidebarToggle}
                    toolbarEnd={companionToggle}
                  />
                  <RevisionDialog
                    open={historyOpen}
                    onOpenChange={setHistoryOpen}
                    bookId={bookId}
                    targetType={selected.kind}
                    targetId={selected.id}
                    currentTitle={documentValue.title}
                    currentContent={documentValue.content}
                    onRestored={refresh}
                  />
                </>
              ) : (
                <>
                  <div className="document-empty-toolbar">
                    {sidebarToggle}
                    {companionToggle}
                  </div>
                  <EmptyState
                    title={
                      loaded
                        ? isEntryRoute(route)
                          ? entryRouteConfig[route].emptyTitle
                          : "暂无章节"
                        : "正在载入"
                    }
                  >
                    {loaded && (
                      <Button
                        onClick={() =>
                          isEntryRoute(route)
                            ? createEntryDirect(entryRouteConfig[route].kind, "")
                            : createChapterDirect(null)
                        }
                      >
                        <Plus size={14} />
                        {isEntryRoute(route)
                          ? `新建${entryRouteConfig[route].itemLabel}`
                          : "新建章节"}
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
                bookId={bookId ?? undefined}
                showHeading={false}
                toolbarStart={sidebarToggle}
                changes={currentPending ? [currentPending] : []}
                totalCount={data.changes.length}
                onChanged={refresh}
                focusedId={currentPending?.id ?? null}
                beforeApply={(change) =>
                  !(editor.current?.dirty && change.target_id === selected?.id)
                }
                beforeApplyAll={() =>
                  !(
                    editor.current?.dirty &&
                    data.changes.some((change) => change.target_id === selected?.id)
                  )
                }
              />
            </div>
          )}

          {route === "trash" && (
            <div className="trash-content">
              <div className="panel-toolbar">
                <div className="row editor-info">
                  {sidebarToggle}
                  <span className="toolbar-divider" aria-hidden="true" />
                  <span className="editor-meta">
                    <Trash2 size={14} />
                    <span>回收站</span>
                    {activeTrashItem && (
                      <>
                        <span className="editor-meta-sep" aria-hidden="true">
                          ·
                        </span>
                        <span>{activeTrashItem.kindLabel}</span>
                      </>
                    )}
                  </span>
                </div>
                {activeTrashItem && (
                  <div className="row editor-actions">
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void restoreTrashItem({
                          kind: activeTrashItem.kind,
                          id: activeTrashItem.id,
                          title: activeTrashItem.title,
                        })
                      }
                    >
                      <RotateCcw size={14} />
                      还原
                    </Button>
                    <Button
                      variant="danger"
                      disabled={busy}
                      onClick={() =>
                        setTrashConfirm({
                          kind: activeTrashItem.kind,
                          id: activeTrashItem.id,
                          title: activeTrashItem.title,
                        })
                      }
                    >
                      <Trash2 size={14} />
                      彻底删除
                    </Button>
                  </div>
                )}
              </div>
              {activeTrashItem ? (
                <div className="trash-preview">
                  <h2 className="trash-preview-title">{activeTrashItem.title || "无标题"}</h2>
                  <p className="trash-preview-meta">
                    {activeTrashItem.kindLabel}
                    {activeTrashItem.deletedAt
                      ? ` · 删除于 ${new Date(activeTrashItem.deletedAt).toLocaleString("zh-CN")}`
                      : ""}
                  </p>
                  <pre className="trash-preview-body">{activeTrashItem.content || "（空白）"}</pre>
                </div>
              ) : (
                <EmptyState title="回收站为空">
                  删除的章节和设定会暂存在这里，支持还原或彻底清除。
                </EmptyState>
              )}
            </div>
          )}
        </main>

        <div
          className={`workspace-chat ${
            route !== "discussion" && !(editing && companionOpen) ? "is-collapsed" : ""
          }`}
          aria-hidden={route !== "discussion" && !(editing && companionOpen)}
        >
          {editing && companionOpen && (
            <div
              className="companion-resize-handle"
              role="separator"
              aria-orientation="vertical"
              aria-label="调整 AI 面板宽度"
              title="拖动调整宽度（双击恢复默认）"
              onPointerDown={startCompanionResize}
              onDoubleClick={() => updateCompanionWidth(DEFAULT_COMPANION_WIDTH)}
            />
          )}
          {bookId && (
            <ChatPanel
              key={bookId}
              bookId={bookId}
              chapters={data.chapters}
              entries={data.entries}
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
                placeholder="搜索章节、角色、背景设定、提纲与笔记…"
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
                hits.map((hit, index) => {
                  const matchedEntry =
                    hit.target_type === "entry"
                      ? data.entries.find((e) => e.id === hit.target_id)
                      : undefined;
                  return (
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
                      {docIcon(
                        hit.target_type === "chapter" ? "chapter" : (matchedEntry?.kind ?? "note"),
                        16,
                      )}
                      <div className="spotlight-hit-body">
                        <div className="spotlight-hit-top">
                          <strong>{hit.title}</strong>
                          <span className="spotlight-hit-kind">
                            {hit.target_type === "chapter"
                              ? "章节"
                              : matchedEntry
                                ? entryKindLabel(matchedEntry.kind)
                                : "设定"}
                          </span>
                        </div>
                        <small>{hit.snippet.replace(/<[^>]*>/g, "")}</small>
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>

      <Dialog
        open={groupForm !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setGroupForm(null);
        }}
        title={
          groupForm?.target === "volume"
            ? groupForm.id
              ? "编辑分卷"
              : "新建分卷"
            : groupForm?.id
              ? "编辑文件夹"
              : "新建文件夹"
        }
      >
        <Alert>{error}</Alert>
        {groupForm && (
          <div className="stack">
            <Input
              label="名称"
              autoFocus
              value={groupForm.title}
              onChange={(event) => setGroupForm({ ...groupForm, title: event.target.value })}
            />
            <Input
              label="副标题（可选）"
              placeholder="一句话说明本分组的内容…"
              value={groupForm.description}
              onChange={(event) => setGroupForm({ ...groupForm, description: event.target.value })}
            />
            <div className="dialog-actions">
              <Button disabled={busy} onClick={() => setGroupForm(null)}>
                取消
              </Button>
              <Button
                variant="primary"
                disabled={busy || !groupForm.title.trim()}
                onClick={() => void submitGroupForm()}
              >
                {busy ? "保存中" : "确认"}
              </Button>
            </div>
          </div>
        )}
      </Dialog>

      <Dialog
        open={renameForm !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setRenameForm(null);
        }}
        title={renameForm?.target === "chapter" ? "重命名章节" : "重命名条目"}
      >
        <Alert>{error}</Alert>
        {renameForm && (
          <div className="stack">
            <Input
              label="名称"
              autoFocus
              value={renameForm.title}
              onChange={(event) => setRenameForm({ ...renameForm, title: event.target.value })}
            />
            <div className="dialog-actions">
              <Button disabled={busy} onClick={() => setRenameForm(null)}>
                取消
              </Button>
              <Button
                variant="primary"
                disabled={busy || !renameForm.title.trim()}
                onClick={() => void submitRenameForm()}
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
              value={folderPrompt.title}
              onChange={(event) => setFolderPrompt({ ...folderPrompt, title: event.target.value })}
            />
            <div className="dialog-actions">
              <Button disabled={busy} onClick={() => setFolderPrompt(null)}>
                取消
              </Button>
              <Button
                variant="primary"
                disabled={busy || !folderPrompt.title.trim()}
                onClick={() => {
                  const targetEntry = data.entries.find((e) => e.id === folderPrompt.entryId);
                  if (!targetEntry) return;
                  void moveEntry(targetEntry, targetEntry.kind, folderPrompt.title).then(() =>
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
        description={
          deletion?.kind === "volume"
            ? `删除分卷「${deletion.title}」？其中的章节将保留为未分卷。`
            : deletion?.kind === "folder"
              ? `删除文件夹「${deletion.title}」？其中的条目将移出文件夹。`
              : `将「${deletion?.title ?? ""}」移入回收站？可以在回收站中恢复。`
        }
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
                  if (item.kind === "chapter" || item.kind === "entry") {
                    setOpenTabs((prev) =>
                      prev.filter((t) => !(t.kind === item.kind && t.id === item.id)),
                    );
                    if (selected?.id === item.id) setSelected(null);
                  }
                  setDeletion(null);
                });
            }}
          >
            {deletion?.kind === "chapter" || deletion?.kind === "entry" ? "移入回收站" : "删除"}
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={trashConfirm !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setTrashConfirm(null);
        }}
        title={trashConfirm === "empty" ? "清空回收站" : "彻底删除"}
        description={
          trashConfirm === "empty"
            ? `确定永久清除回收站中的全部 ${trashTotalCount} 个项目吗？此操作无法撤销。`
            : `确定永久删除「${trashConfirm?.title ?? ""}」吗？此操作无法撤销。`
        }
      >
        <Alert>{error}</Alert>
        <div className="dialog-actions">
          <Button disabled={busy} onClick={() => setTrashConfirm(null)}>
            取消
          </Button>
          <Button variant="danger" disabled={busy} onClick={() => void confirmPurgeTrash()}>
            确认清除
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
