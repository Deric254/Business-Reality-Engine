import { useCallback, useEffect, useState } from "react";
import { systemApi, type SystemLink } from "../api/client";
import { errText } from "./errors";

/** The three systems and the live state of their links. Polls quickly while one is being set up or checked, slowly otherwise. */
export function useSystemLinks(tenantId: string, fast = false) {
  const [links, setLinks] = useState<SystemLink[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const load = useCallback(() => systemApi.list(tenantId).then((l) => { setLinks(l); setErr(null); }).catch((e) => setErr(errText(e))).finally(() => setLoaded(true)), [tenantId]);
  useEffect(() => { void load(); }, [load]);
  const hurry = fast || links.some((l) => l.connected && (l.checking || l.state === "waiting"));
  useEffect(() => { const t = setInterval(load, hurry ? 3000 : 15000); return () => clearInterval(t); }, [load, hurry]);
  return { links, reload: load, err, loaded };
}
