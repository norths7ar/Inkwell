import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { BookOpen, Trash2, Upload } from "lucide-react";
import { Alert, Button, Dialog, Input, Textarea } from "./components/ui";
import { errorText, remove, request, upload } from "./lib/api";
import { bookSchema, type Book } from "./lib/schema";
import { EventsProvider, useEvents } from "./lib/events";
import { Workspace, type AppRoute } from "./features/Workspace";
import { Settings } from "./features/Settings";

const LAST_BOOK_KEY = "inkwell:last-book-id";
const ALLOWED_COVER_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_COVER_BYTES = 5 * 1024 * 1024;

type BookFormState = {
  id?: string;
  title: string;
  description: string;
  coverPath: string;
  coverUpdatedAt: string;
  coverFile: File | null;
  coverPreviewUrl: string | null;
  removeCover: boolean;
};

function InkwellApp() {
  const [books, setBooks] = useState<Book[]>([]);
  const [route, setRoute] = useState<AppRoute>("shelf");
  const [activeBookId, setActiveBookId] = useState<string | null>(null);
  const [lastBookId, setLastBookId] = useState<string | null>(() => {
    try {
      return localStorage.getItem(LAST_BOOK_KEY);
    } catch {
      return null;
    }
  });
  const [searchOpen, setSearchOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const refreshBooks = useCallback(() => setRevision((value) => value + 1), []);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [form, setForm] = useState<BookFormState | null>(null);
  const coverInputRef = useRef<HTMLInputElement | null>(null);
  const [deletion, setDeletion] = useState<Book | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  const openBook = useCallback(
    (book: Book, nextRoute: Exclude<AppRoute, "shelf"> = "chapters", openSearch = false) => {
      try {
        localStorage.setItem(LAST_BOOK_KEY, book.id);
      } catch {
        /* Optional preference. */
      }
      setLastBookId(book.id);
      setActiveBookId(book.id);
      setRoute(nextRoute);
      setSearchOpen(openSearch);
    },
    [],
  );

  useEvents((event) => {
    if (
      event.type === "resync_required" ||
      (event.type === "content_changed" && event.resource === "book")
    )
      refreshBooks();
  });

  useEffect(() => {
    let current = true;
    const timer = setTimeout(() => {
      void request("/books", z.array(bookSchema))
        .then((items) => {
          if (!current) return;
          setBooks(items);
          setLoaded(true);
          setError("");
        })
        .catch((failure: unknown) => {
          if (current) setError(errorText(failure));
        });
    }, 60);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [revision]);

  const closeForm = useCallback(() => {
    setForm((current) => {
      if (current?.coverPreviewUrl) URL.revokeObjectURL(current.coverPreviewUrl);
      return null;
    });
  }, []);

  function pickCoverFile(file: File | undefined) {
    if (!file || !form) return;
    if (!ALLOWED_COVER_TYPES.has(file.type)) {
      setError("封面仅支持 JPG、PNG 或 WebP 图片。");
      return;
    }
    if (file.size > MAX_COVER_BYTES) {
      setError("封面图片大小不能超过 5 MB。");
      return;
    }
    setError("");
    const previewUrl = URL.createObjectURL(file);
    setForm((current) => {
      if (!current) {
        URL.revokeObjectURL(previewUrl);
        return null;
      }
      if (current.coverPreviewUrl) URL.revokeObjectURL(current.coverPreviewUrl);
      return {
        ...current,
        coverFile: file,
        coverPreviewUrl: previewUrl,
        removeCover: false,
      };
    });
  }

  async function saveBook() {
    if (!form) return;
    setBusy(true);
    setError("");
    let savedId = form.id;
    try {
      let item = await request(form.id ? `/books/${form.id}` : "/books", bookSchema, {
        method: form.id ? "PATCH" : "POST",
        body: { title: form.title, description: form.description },
      });
      savedId = item.id;
      if (form.coverFile) {
        item = await upload(`/books/${item.id}/cover`, bookSchema, form.coverFile);
      } else if (form.id && form.removeCover && form.coverPath) {
        item = await request(`/books/${item.id}/cover`, bookSchema, { method: "DELETE" });
      }
      refreshBooks();
      closeForm();
      if (!form.id) openBook(item);
    } catch (failure) {
      if (savedId && !form.id) {
        refreshBooks();
        setForm((current) => (current ? { ...current, id: savedId } : null));
      }
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  const activeBook = books.find((item) => item.id === activeBookId) ?? null;
  const fallbackBook = books.find((item) => item.id === lastBookId) ?? books[0] ?? null;
  const targetBook = activeBook ?? fallbackBook;

  return (
    <>
      <Workspace
        books={books}
        booksLoaded={loaded}
        booksError={error}
        activeBook={activeBook}
        targetBook={targetBook}
        route={route}
        searchOpen={searchOpen}
        onSearchOpenChange={setSearchOpen}
        onOpenBook={openBook}
        onRouteChange={(next) => {
          if (next !== "shelf" && targetBook) {
            openBook(targetBook, next);
          } else {
            setRoute(next);
            if (next === "shelf") refreshBooks();
          }
        }}
        onRefreshBooks={refreshBooks}
        onNewBook={() => {
          setError("");
          setForm({
            title: "",
            description: "",
            coverPath: "",
            coverUpdatedAt: "",
            coverFile: null,
            coverPreviewUrl: null,
            removeCover: false,
          });
        }}
        onEditBook={(book) => {
          setError("");
          setForm({
            id: book.id,
            title: book.title,
            description: book.description,
            coverPath: book.cover_path,
            coverUpdatedAt: book.updated_at,
            coverFile: null,
            coverPreviewUrl: null,
            removeCover: false,
          });
        }}
        onDeleteBook={setDeletion}
        onSettings={() => setSettingsOpen(true)}
      />
      <Settings
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        onSaved={refreshBooks}
        activeBook={targetBook}
        books={books}
      />
      <Dialog
        open={form !== null}
        onOpenChange={(open) => {
          if (!open && !busy) closeForm();
        }}
        title={form?.id ? "编辑作品" : "新建作品"}
      >
        <Alert>{error}</Alert>
        {form &&
          (() => {
            const previewUrl =
              form.coverPreviewUrl ??
              (!form.removeCover && form.id && form.coverPath
                ? `/api/books/${form.id}/cover?v=${encodeURIComponent(form.coverUpdatedAt)}`
                : null);
            return (
              <div className="stack">
                <div className="book-cover-editor">
                  <div className="book-cover-thumb">
                    {previewUrl ? (
                      <img
                        className="book-cover-image"
                        src={previewUrl}
                        alt={form.title || "封面预览"}
                      />
                    ) : (
                      <div className="book-cover-thumb-empty">
                        <BookOpen size={22} />
                      </div>
                    )}
                  </div>
                  <div className="book-cover-controls">
                    <input
                      ref={coverInputRef}
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      hidden
                      onChange={(event) => {
                        pickCoverFile(event.target.files?.[0]);
                        event.target.value = "";
                      }}
                    />
                    <div className="row">
                      <Button disabled={busy} onClick={() => coverInputRef.current?.click()}>
                        <Upload size={14} />
                        {previewUrl ? "更换封面" : "上传封面"}
                      </Button>
                      {previewUrl && (
                        <Button
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            setForm((current) => {
                              if (!current) return null;
                              if (current.coverPreviewUrl)
                                URL.revokeObjectURL(current.coverPreviewUrl);
                              return {
                                ...current,
                                coverFile: null,
                                coverPreviewUrl: null,
                                removeCover: true,
                              };
                            })
                          }
                        >
                          <Trash2 size={14} />
                          移除封面
                        </Button>
                      )}
                    </div>
                    <span className="muted">支持 JPG、PNG、WebP，建议 2:3 比例，不超过 5 MB</span>
                  </div>
                </div>
                <Input
                  label="作品名称"
                  value={form.title}
                  autoFocus
                  onChange={(event) => setForm({ ...form, title: event.target.value })}
                />
                <Textarea
                  label="简介"
                  rows={4}
                  value={form.description}
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                />
                <div className="dialog-actions">
                  <Button disabled={busy} onClick={closeForm}>
                    取消
                  </Button>
                  <Button
                    variant="primary"
                    disabled={busy || !form.title.trim()}
                    onClick={() => void saveBook()}
                  >
                    {busy ? "保存中" : "保存作品"}
                  </Button>
                </div>
              </div>
            );
          })()}
      </Dialog>
      <Dialog
        open={deletion !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setDeletion(null);
        }}
        title="删除作品"
        description={`「${deletion?.title ?? ""}」的章节、设定与会话也会删除，此操作无法撤销。`}
      >
        <Alert>{error}</Alert>
        <div className="row end">
          <Button onClick={() => setDeletion(null)}>取消</Button>
          <Button
            variant="danger"
            disabled={busy}
            onClick={() => {
              if (!deletion) return;
              const removedId = deletion.id;
              setBusy(true);
              void remove(`/books/${removedId}`)
                .then(() => {
                  setDeletion(null);
                  if (activeBookId === removedId) {
                    setActiveBookId(null);
                    setRoute("shelf");
                  }
                  refreshBooks();
                })
                .catch((failure: unknown) => setError(errorText(failure)))
                .finally(() => setBusy(false));
            }}
          >
            确认删除
          </Button>
        </div>
      </Dialog>
    </>
  );
}

export default function App() {
  return (
    <EventsProvider>
      <InkwellApp />
    </EventsProvider>
  );
}
