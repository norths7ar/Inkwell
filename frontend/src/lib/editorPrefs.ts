import { z } from "zod";

const EDITOR_PREFS_KEY = "inkwell:editor-prefs";

const editorPrefsSchema = z.object({
  fontFamily: z.enum(["serif", "sans"]).default("serif"),
  fontSize: z.number().min(13).max(24).default(16),
  lineHeight: z.number().min(1.5).max(2.6).default(2.1),
  maxWidth: z.enum(["narrow", "medium", "wide"]).default("medium"),
});

export type EditorPrefs = z.infer<typeof editorPrefsSchema>;

export const DEFAULT_EDITOR_PREFS: EditorPrefs = {
  fontFamily: "serif",
  fontSize: 16,
  lineHeight: 2.1,
  maxWidth: "medium",
};

const WIDTH_MAP: Record<EditorPrefs["maxWidth"], string> = {
  narrow: "640px",
  medium: "760px",
  wide: "920px",
};

export function loadEditorPrefs(): EditorPrefs {
  try {
    const raw = localStorage.getItem(EDITOR_PREFS_KEY);
    if (!raw) return DEFAULT_EDITOR_PREFS;
    const parsed = editorPrefsSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : DEFAULT_EDITOR_PREFS;
  } catch {
    return DEFAULT_EDITOR_PREFS;
  }
}

export function saveEditorPrefs(prefs: EditorPrefs): void {
  try {
    localStorage.setItem(EDITOR_PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* Optional preference. */
  }
  applyEditorPrefs(prefs);
}

export function applyEditorPrefs(prefs: EditorPrefs): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement.style;
  root.setProperty(
    "--editor-font-family",
    prefs.fontFamily === "serif"
      ? 'Georgia, "Noto Serif SC", "Microsoft YaHei", serif'
      : 'var(--font-sans, "Inter", "Segoe UI", "Microsoft YaHei", sans-serif)',
  );
  root.setProperty("--editor-font-size", `${prefs.fontSize}px`);
  root.setProperty("--editor-line-height", String(prefs.lineHeight));
  root.setProperty("--editor-max-width", WIDTH_MAP[prefs.maxWidth]);
}
