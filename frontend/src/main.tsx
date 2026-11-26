import { createRoot } from "react-dom/client";
import App from "./App";
import { applyEditorPrefs, loadEditorPrefs } from "./lib/editorPrefs";
import "./styles.css";

applyEditorPrefs(loadEditorPrefs());

const root = document.getElementById("root");
if (!root) throw new Error("Application root is missing");
createRoot(root).render(<App />);
