import { useEffect, useState } from "react";
import { fetchChatModelCatalog, type ChatModelCatalog } from "../../data/chat";
import { useWorkspaceI18n } from "./i18n";

export function ModelConfigurationField({ endpointId, id, label, value, onChange }: {
  endpointId: string; id: string; label: string; value: unknown;
  onChange?: (value: string) => void;
}) {
  const { t } = useWorkspaceI18n();
  const [catalog, setCatalog] = useState<ChatModelCatalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setCatalog(null);
    setLoading(Boolean(endpointId));
    if (!endpointId) return;
    fetchChatModelCatalog(endpointId, abort.signal).then((result) => {
      if (!abort.signal.aborted && result.endpoint_id === endpointId) setCatalog(result);
    }).catch(() => {}).finally(() => {
      if (!abort.signal.aborted) setLoading(false);
    });
    return () => abort.abort();
  }, [endpointId, revision]);
  const current = catalog?.endpoint_id === endpointId ? catalog : null;
  const choices = current?.available ? current.models : [];
  return <label htmlFor={id}>
    <span>{label}</span>
    <input aria-label={label} id={id} list={choices.length ? `${id}-choices` : undefined}
      onChange={onChange ? (event) => onChange(event.target.value) : undefined}
      readOnly={!onChange} type="text" value={typeof value === "string" ? value : ""} />
    {choices.length ? <datalist id={`${id}-choices`}>{choices.map((model) =>
      <option key={model.id} label={model.label} value={model.id} />)}</datalist> : null}
    <span className="personal-model-catalog-note" role="status">
      {loading ? t("models.loading") : choices.length
        ? t(current?.compatibility_applied ? "models.compatibility" : endpointId === "claude-code" ? "models.configured" : "models.loaded", { count: choices.length })
        : t(endpointId ? "models.unavailable" : "models.selectEndpoint")}
      {endpointId && !loading ? <button onClick={(event) => { event.preventDefault(); setRevision((old) => old + 1); }} type="button">{t("models.reload")}</button> : null}
    </span>
  </label>;
}
