import { useCallback, useEffect, useState } from "react";
import { z } from "zod";
import { Alert, Button, Dialog, Input, Textarea } from "./components/ui";
import { errorText, remove, request } from "./lib/api";
import { bookSchema, type Book } from "./lib/schema";
import { EventsProvider, useEvents } from "./lib/events";
import { Workspace, type AppRoute } from "./features/Workspace";
import { Settings } from "./features/Settings";

const LAST_BOOK_KEY = "inkwell:last-book-id";

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
  const [form, setForm] = useState<{ id?: string; title: string; description: string } | null>(
    null,
  );
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

  async function saveBook() {
    if (!form) return;
    setBusy(true);
    setError("");
    try {
      const item = await request(form.id ? `/books/${form.id}` : "/books", bookSchema, {
        method: form.id ? "PATCH" : "POST",
        body: { title: form.title, description: form.description },
      });
      refreshBooks();
      setForm(null);
      if (!form.id) openBook(item);
    } catch (failure) {
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
          setForm({ title: "", description: "" });
        }}
        onEditBook={(book) => {
          setError("");
          setForm({ id: book.id, title: book.title, description: book.description });
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
          if (!open && !busy) setForm(null);
        }}
        title={form?.id ? "编辑作品" : "新建作品"}
      >
        <Alert>{error}</Alert>
        {form && (
          <div className="stack">
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
              <Button disabled={busy} onClick={() => setForm(null)}>
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
        )}
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
