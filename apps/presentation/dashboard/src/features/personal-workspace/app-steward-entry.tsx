import {useEffect, useRef, useState, type ReactNode} from "react";
import {Button} from "../../components/ui/button";
import {fetchAppStewardTarget, type BoundConversationTarget} from "../../data/chat";
import {useWorkspaceI18n} from "./i18n";

/** One read-only resolver for both App entrypoints. Pending/failed discovery
 * never exposes the ordinary manager queue. Explicit bound routes bypass it.
 */
export function useAppStewardTarget(enabled: boolean) {
  const [read, setRead] = useState<{enabled: boolean; revision: number; ready: boolean;
    target: BoundConversationTarget | null; unavailable: boolean}>(
    {enabled, revision: 0, ready: false, target: null, unavailable: false});
  // Invalidate in the render that changes scope, before a composer can submit
  // using a previous ordinary result. React retries this component immediately.
  if (read.enabled !== enabled) setRead({...read, enabled, ready: false, target: null, unavailable: false});
  const retry = () => setRead(value => ({...value, revision: value.revision + 1, ready: false, target: null, unavailable: false}));
  useEffect(() => {
    window.addEventListener("loopx:app-steward-changed", retry);
    return () => window.removeEventListener("loopx:app-steward-changed", retry);
  }, []);
  const revision = read.revision;
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    const settle = (target: BoundConversationTarget | null, unavailable: boolean) => {
      if (active) setRead(value => value.enabled === enabled && value.revision === revision
        ? {...value, ready: true, target, unavailable} : value);
    };
    void fetchAppStewardTarget(controller.signal).then(target => settle(target, false))
      .catch(() => settle(null, true)).finally(() => clearTimeout(timeout));
    return () => {active = false; controller.abort(); clearTimeout(timeout);};
  }, [enabled, revision]);
  const ready = enabled && read.enabled === enabled && read.ready;
  return {target: ready ? read.target : null, loading: enabled && !ready,
    unavailable: ready && read.unavailable, retry};
}

export function AppStewardEntry({children, enabled, intercept, onOpen}: {
  children: (entry: ReturnType<typeof useAppStewardTarget>) => ReactNode;
  enabled: boolean;
  intercept: boolean;
  onOpen: (target: BoundConversationTarget) => void;
}) {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const entry = useAppStewardTarget(enabled);
  const open = useRef(onOpen);
  open.current = onOpen;
  useEffect(() => {if (intercept && entry.target) open.current(entry.target);}, [intercept, entry.target]);
  const blocked = intercept && (entry.loading || entry.unavailable || Boolean(entry.target));
  const settings = new URL(window.location.href);
  settings.searchParams.set("view", "overview");
  settings.searchParams.set("settings_scope", "machine");
  settings.searchParams.set("settings_tab", "lark");
  return <>
    {/* Keep the workspace and its readers mounted across entry/navigation.
        The same readback gates its queue while discovery is unresolved. */}
    <div style={{display: blocked ? "none" : "contents"}}>{children(entry)}</div>
    {blocked ? <main className="mx-auto flex min-h-dvh max-w-xl flex-col justify-center gap-5 p-6 text-slate-900 dark:text-zinc-100">
      <h1 className="text-xl font-semibold">{zh ? "LoopX 管家" : "LoopX steward"}</h1>
      {!entry.unavailable ? <p role="status">{zh ? "正在打开管家对话…" : "Opening the steward conversation…"}</p> : <>
        <p role="alert">{zh ? "原管家会话暂不可用。请检查所选连接后重试。" : "The original steward conversation is unavailable. Check the selected connection and retry."}</p>
        <div className="flex flex-wrap gap-3">
          <Button onClick={entry.retry}>{zh ? "重试" : "Retry"}</Button>
          <a className="inline-flex min-h-9 items-center underline underline-offset-4" href={settings.href}>{zh ? "打开会话设置" : "Open conversation settings"}</a>
        </div>
      </>}
    </main> : null}
  </>;
}
