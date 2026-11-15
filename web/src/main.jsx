import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/600.css";
import "@fontsource/space-grotesk/700.css";
import App from "./App.jsx";
import { SettingsProvider } from "./settings.jsx";
import "./shell.css";

// Apply the saved theme before the first paint (no flash of the wrong theme).
try { document.documentElement.dataset.theme = localStorage.getItem("theme") || "dark"; } catch { document.documentElement.dataset.theme = "dark"; }

createRoot(document.getElementById("root")).render(
  <SettingsProvider>
    <App />
  </SettingsProvider>
);
