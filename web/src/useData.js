import { useEffect, useState } from "react";
import { getJSON } from "./api.js";
import { useSettings } from "./settings.jsx";

// Fetch a JSON endpoint; refetches when the path changes or the dashboard settings (mode, plan date) change.
export function useData(path) {
  const { rev } = useSettings();
  const [state, setState] = useState({ data: null, error: null, loading: true });
  useEffect(() => {
    let live = true;
    setState((s) => ({ data: s.data, error: null, loading: true }));
    getJSON(path)
      .then((data) => live && setState({ data, error: null, loading: false }))
      .catch((error) => live && setState({ data: null, error, loading: false }));
    return () => { live = false; };
  }, [path, rev]);
  return state;
}
