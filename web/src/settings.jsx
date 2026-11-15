import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { getJSON, postJSON } from "./api.js";

// Data mode (always real on the dashboard) and "plan as of" date, held by the local API server; `rev` changes after every update so pages
// refetch their data.
const Ctx = createContext({ settings: null, rev: 0, update: async () => {}, error: null });

export function SettingsProvider({ children }) {
  const [settings, setSettings] = useState(null);
  const [rev, setRev] = useState(0);
  const [error, setError] = useState(null);

  useEffect(() => { getJSON("/settings").then(setSettings).catch(setError); }, []);

  const update = useCallback(async (patch) => {
    setError(null);
    try {
      const s = await postJSON("/settings", patch);
      setSettings(s);
      setRev((r) => r + 1);
    } catch (e) {
      setError(e);
    }
  }, []);

  return <Ctx.Provider value={{ settings, rev, update, error }}>{children}</Ctx.Provider>;
}

export function useSettings() {
  return useContext(Ctx);
}
