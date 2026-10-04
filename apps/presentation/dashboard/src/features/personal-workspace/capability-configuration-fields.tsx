import { useId, type ReactNode } from "react";

import type { CapabilityConfigurationEditor } from "../../data/chat";
import { PeriodicReportScheduleField } from "./periodic-report-schedule-field";

type FieldCopy = Record<string, { description?: string; label?: string; options?: Record<string, string> }>;
type ConfigurationField = CapabilityConfigurationEditor["fields"][number];
type FieldValue = boolean | number | string | string[] | Record<string, unknown> | null;
type FieldChange = (key: string, value: FieldValue) => void;

type ConfigurationFieldProps = Readonly<{
  copy: FieldCopy;
  field: ConfigurationField;
  id: string;
  onChange?: FieldChange;
  value: unknown;
  timezone: string;
}>;

function ConfigurationFieldControl({ copy, field, id, onChange, value, timezone }: ConfigurationFieldProps) {
  const label = copy[field.key]?.label ?? field.label;
  const readOnly = !onChange;

  if (field.input_kind === "periodic_report_schedule") {
    return <PeriodicReportScheduleField id={id} value={value} timezone={timezone}
      onChange={onChange ? (schedule) => onChange(field.key, schedule) : undefined} />;
  }

  if (field.input_kind === "boolean") {
    return (
      <label className="is-boolean" htmlFor={id}>
        <span>{label}</span>
        <input checked={value === true} id={id} onChange={onChange ? (event) => onChange(field.key, event.target.checked) : undefined} readOnly={readOnly} role="switch" type="checkbox" />
      </label>
    );
  }
  if (field.input_kind === "select") {
    return (
      <label htmlFor={id}>
        <span>{label}</span>
        <select id={id} onChange={onChange ? (event) => onChange(field.key, event.target.value) : undefined} value={typeof value === "string" ? value : ""}>
          <option value="" />
          {(field.options ?? []).map((option) => <option key={option} value={option}>{copy[field.key]?.options?.[option] ?? option}</option>)}
        </select>
      </label>
    );
  }
  if (field.input_kind === "string_list") {
    return (
      <label htmlFor={id}>
        <span>{label}</span>
        <textarea id={id} onChange={onChange ? (event) => onChange(field.key, event.target.value.split(/\r?\n/u).filter(Boolean)) : undefined} readOnly={readOnly} rows={4} value={Array.isArray(value) ? value.join("\n") : ""} />
      </label>
    );
  }
  const numeric = field.input_kind === "number";
  const modelSuggestions = field.key === "executor_model"
    ? ["gpt-6-sol", "gpt-6-luna", "gpt-6-astra"] : [];
  return (
    <label htmlFor={id}>
      <span>{label}</span>
      <input
        id={id}
        list={modelSuggestions.length ? `${id}-suggestions` : undefined}
        max={field.maximum}
        min={field.minimum}
        onChange={onChange ? (event) => onChange(field.key, numeric ? Number(event.target.value) : event.target.value) : undefined}
        readOnly={readOnly}
        required={field.required}
        type={numeric ? "number" : "text"}
        value={typeof value === "number" || typeof value === "string" ? value : ""}
      />
      {modelSuggestions.length ? <datalist id={`${id}-suggestions`}>{modelSuggestions.map((model) => <option key={model} value={model} />)}</datalist> : null}
    </label>
  );
}

type CapabilityConfigurationFieldsProps = Readonly<{
  copy?: FieldCopy;
  disabled?: boolean;
  editor: CapabilityConfigurationEditor;
  enabledAction?: ReactNode;
  omitKeys?: readonly string[];
  onChange?: FieldChange;
  value: Record<string, unknown>;
}>;

export function CapabilityConfigurationFields({
  copy = {},
  disabled = false,
  editor,
  enabledAction,
  omitKeys = [],
  onChange,
  value,
}: CapabilityConfigurationFieldsProps) {
  const idPrefix = useId();
  const omitted = new Set(omitKeys);

  return (
    <fieldset className="personal-capability-fields" disabled={disabled}>
      {editor.fields.filter((field) => !omitted.has(field.key)).map((field) => {
        const control = <ConfigurationFieldControl
          copy={copy}
          field={field}
          id={`${idPrefix}-${field.key.replace(/[^a-z0-9_-]/gi, "-")}`}
          key={field.key}
          onChange={onChange}
          value={value[field.key]}
          timezone={String(value.timezone ?? "UTC")}
        />;
        return field.key === "enabled" && field.input_kind === "boolean"
          ? <div className="personal-capability-enabled-row" key={field.key}>{control}{enabledAction}</div>
          : control;
      })}
    </fieldset>
  );
}
