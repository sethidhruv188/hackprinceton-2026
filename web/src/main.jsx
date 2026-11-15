import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import App from "./App.jsx";
import { SettingsProvider } from "./settings.jsx";
import "./shell.css";

// Apply the saved theme before the first paint (no flash of the wrong theme).
try { document.documentElement.dataset.theme = localStorage.getItem("theme") || "light"; } catch { document.documentElement.dataset.theme = "light"; }

createRoot(document.getElementById("root")).render(
  <SettingsProvider>
    <App />
  </SettingsProvider>
);
