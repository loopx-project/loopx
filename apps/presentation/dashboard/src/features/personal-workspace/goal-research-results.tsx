import {useEffect, useRef, useState} from "react";
import {RefreshCw} from "lucide-react";
import {fetchPresentationProjection, type PresentationProjectionEnvelope} from "../../data/decision-research";
import {parsePresentationSurfaceCollectionResponse, type PresentationSurface, type RunGoal} from "../../data/status";
import {DecisionResearchSurface} from "../../views/decision-research-surface";
import {ResultCatalogReturn, ResultCatalogRow, useResultCatalogFocus} from "./result-catalog";

export type GoalResearchApi = {indexUrl: string; detailUrl: string; goals: RunGoal[]};
type Result = {surface: PresentationSurface; projection?: PresentationProjectionEnvelope; error?: string};
const resultKey = (surface: PresentationSurface) => JSON.stringify([surface.extension_id, surface.surface_id,
  surface.extension_revision, surface.state === "ready" || surface.state === "review_due" ? surface.detail_ref.payload_sha256 : surface.state]);

/** The active Goal's published research; the extension index owns availability. */
export function GoalResearchResults({goalId, api, zh, hideEmpty = false, catalogOnly = false}: {
  goalId: string; api: GoalResearchApi; zh: boolean; hideEmpty?: boolean; catalogOnly?: boolean;
}) {
  const [results, setResults] = useState<Result[]>([]);
  const [selected, setSelected] = useState<Result | null>(null);
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState(true);
  const [listed, setListed] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const chosen = useRef<string | null>(null);
  const focus = useResultCatalogFocus();
  useEffect(() => {
    chosen.current = null;
    setReading(false); setResults([]);
    void list();
    return () => {generation.current++; focus.cancel();};
  }, [goalId, api.indexUrl, api.detailUrl, catalogOnly]);

  async function projection(surface: PresentationSurface): Promise<Result> {
    if (surface.state !== "ready" && surface.state !== "review_due") return {surface};
    return {surface, projection: await fetchPresentationProjection(api.detailUrl, surface)};
  }

  async function list() {
    const current = ++generation.current;
    focus.cancel();
    setSelected(null); setError(""); setBusy(true); setListed(false);
    if (!catalogOnly) setResults([]);
    try {
      const response = await fetch(api.indexUrl, {cache: "no-store"});
      // The optional index may be absent. Clear stale rows without turning
      // that empty state into an error; other HTTP and decode failures surface.
      if (response.status === 404 || response.status === 410) {
        if (current === generation.current) {setListed(true); setResults([]);}
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const collection = parsePresentationSurfaceCollectionResponse(await response.json());
      if (current !== generation.current) return;
      setListed(true);
      const surfaces = collection.items.filter(surface => surface.goal_id === goalId
        && surface.visibility === "public-safe"
        && surface.surface_kind === "decision_research_dashboard"
        && surface.view_schema === "decision_research_dashboard_v0");
      if (catalogOnly) {
        setResults(surfaces.map(surface => ({surface})));
        if (chosen.current) {
          const surface = surfaces.find(item => resultKey(item) === chosen.current);
          if (!surface) {
            setError(zh ? "上次研究版本已不在当前公开成果中。请返回目录重新选择。" : "The previous research version is no longer in current public results. Return to the catalog to choose again.");
          } else {
            try {
              const result = await projection(surface);
              if (current === generation.current) setSelected(result);
            } catch (failure) {if (current === generation.current) setError(String(failure));}
          }
        }
      } else {
        const next = await Promise.all(surfaces.map(async (surface): Promise<Result> => {
          try {return await projection(surface);}
          catch (failure) {return {surface, error: String(failure)};}
        }));
        if (current === generation.current) setResults(next);
      }
    } catch (failure) {
      if (current === generation.current) {setResults([]); setError(String(failure));}
    } finally {if (current === generation.current) setBusy(false);}
  }

  async function select(surface: PresentationSurface) {
    const current = ++generation.current;
    chosen.current = resultKey(surface);
    setReading(true); setSelected(null); setError(""); setBusy(true);
    focus.reader();
    try {
      const result = await projection(surface);
      if (current === generation.current) {setSelected(result); focus.reader();}
    } catch (failure) {if (current === generation.current) setError(String(failure));}
    finally {if (current === generation.current) setBusy(false);}
  }

  function returnToCatalog() {
    const origin = chosen.current;
    generation.current++;
    chosen.current = null;
    setReading(false); setSelected(null); setBusy(false); setError("");
    focus.row(origin);
  }

  if (hideEmpty && !reading && !busy && !error && !results.length) return null;
  return <section ref={focus.root} tabIndex={-1} className={`goal-team-results goal-research-results${catalogOnly ? " is-catalog" : ""}`}
    aria-label={zh ? "研究成果" : "Research results"} aria-busy={busy}>
    <header><div><h3>{zh ? "研究成果" : "Research results"}</h3></div>
      <button type="button" disabled={busy} onClick={() => void list()}>
        <RefreshCw size={14} aria-hidden="true"/>{zh ? "刷新" : "Refresh"}
      </button></header>
    {busy ? <p role="status">{zh ? "正在核验研究结果…" : "Verifying research results…"}</p> : null}
    {error ? <p role="alert">{zh ? "无法核验研究结果；旧内容已清除。" : "Cannot verify research results; previous content was cleared."} {error}</p> : null}
    {!busy && listed && !results.length ? <p>{zh ? "本 Goal 暂无可读取的公开研究成果。" : "No readable public research results for this Goal yet."}</p> : null}
    {catalogOnly ? <div className="goal-team-results-layout">
      <nav data-result-catalog tabIndex={-1} className="goal-team-result-list" hidden={reading} aria-label={zh ? "选择研究成果" : "Choose a research result"}>
        {results.map(({surface}) => <ResultCatalogRow key={resultKey(surface)} resultKey={resultKey(surface)} title={surface.title}
          source={`${surface.extension_id} · ${surface.surface_id}`} status={surface.state} time={surface.generated_at} zh={zh}
          disabled={busy} selected={chosen.current === resultKey(surface)} onSelect={() => void select(surface)}/>)}
      </nav>
      {reading ? <div data-result-reader tabIndex={-1} className="goal-team-result-reader">
        <ResultCatalogReturn zh={zh} onReturn={returnToCatalog}/>
        {selected ? <DecisionResearchSurface key={resultKey(selected.surface)} goals={api.goals} surface={selected.surface}
          view={selected.projection?.view} projection={selected.projection} zh={zh}/> : null}
      </div> : null}
    </div> : results.map(result => <DecisionResearchSurface key={resultKey(result.surface)}
      goals={api.goals} surface={result.surface} view={result.projection?.view}
      projection={result.projection} viewError={result.error} zh={zh}/>)}
  </section>;
}
