import {useEffect, useState} from "react";
import {RefreshCw} from "lucide-react";
import {fetchPresentationProjection, type DecisionResearchView} from "../../data/decision-research";
import {parsePresentationSurfaceCollectionResponse, type PresentationSurface, type RunGoal} from "../../data/status";
import {DecisionResearchSurface} from "../../views/decision-research-surface";

export type GoalResearchApi = {indexUrl: string; detailUrl: string; goals: RunGoal[]};
type Result = {surface: PresentationSurface; view?: DecisionResearchView; error?: string};

/** The active Goal's published research; the extension index owns availability. */
export function GoalResearchResults({goalId, api, zh}: {goalId: string; api: GoalResearchApi; zh: boolean}) {
  const [results, setResults] = useState<Result[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true;
    setResults([]); setError(""); setBusy(true);
    void (async () => {
      try {
        const response = await fetch(api.indexUrl, {cache: "no-store"});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const collection = parsePresentationSurfaceCollectionResponse(await response.json());
        const surfaces = collection.items.filter(surface => surface.goal_id === goalId
          && surface.visibility === "public-safe"
          && surface.surface_kind === "decision_research_dashboard"
          && surface.view_schema === "decision_research_dashboard_v0");
        const next = await Promise.all(surfaces.map(async (surface): Promise<Result> => {
          if (surface.state !== "ready" && surface.state !== "review_due") return {surface};
          try {
            const projection = await fetchPresentationProjection(api.detailUrl, surface);
            return {surface, view: projection.view};
          } catch (failure) {return {surface, error: String(failure)};}
        }));
        if (current) setResults(next);
      } catch (failure) {if (current) setError(String(failure));}
      finally {if (current) setBusy(false);}
    })();
    return () => {current = false;};
  }, [goalId, api.indexUrl, api.detailUrl, revision]);

  return <section className="goal-team-results goal-research-results min-w-0 [overflow-wrap:anywhere]"
    aria-label={zh ? "研究成果" : "Research results"} aria-busy={busy}>
    <header><div><h3>{zh ? "研究成果" : "Research results"}</h3>
      <p>{zh ? "查看本 Goal 已保存的研究结论、反证和待复核事项。" : "Review this Goal’s saved conclusions, counterevidence and open questions."}</p></div>
      <button type="button" disabled={busy} onClick={() => {setResults([]); setRevision(value => value + 1);}}>
        <RefreshCw size={14} aria-hidden="true"/>{zh ? "刷新" : "Refresh"}
      </button></header>
    {busy ? <p role="status">{zh ? "正在核验研究结果…" : "Verifying research results…"}</p> : null}
    {error ? <p role="alert">{zh ? "无法核验研究结果；旧内容已清除。" : "Cannot verify research results; previous content was cleared."} {error}</p> : null}
    {!busy && !error && !results.length ? <p>{zh ? "本 Goal 暂无可读取的公开研究成果。" : "No readable public research results for this Goal yet."}</p> : null}
    {results.map(result => <DecisionResearchSurface key={`${result.surface.extension_id}:${result.surface.surface_id}`}
      goals={api.goals} surface={result.surface} view={result.view} viewError={result.error}/>) }
  </section>;
}
