/** Public entry of the LoopX data layer. */
export { LoopxApi } from './LoopxApi.ts';
export { LoopXApiAdapter } from './adapter.ts';
export type {
  AgentInfo,
  GeneralSettings,
  HostInfo,
  ModelOption,
  Schedule,
  Section,
  StoredEvent,
  Thread,
  Todo,
  TriageBucket,
  TriageEntry,
  TriageView,
  WorkdirsConfig,
} from './types.ts';
export { loadConfig, type LoopXAdapterConfig } from './config.ts';
export {
  ContractFormatError,
  ReadOnlyError,
  SchemaVersionError,
  SourceUnavailableError,
} from './errors.ts';
export { PLACEHOLDERS, placeholder, placeholderCount } from './placeholders.ts';
export { parseStatusPayload, SUPPORTED_STATUS_SCHEMA_VERSION } from './schema-guard.ts';
export { deriveTriage } from './mappers/triage.ts';
export { mapMessagesToEvents } from './mappers/events.ts';
export { mapSessionsToThreads } from './mappers/threads.ts';
export { mapTodos } from './mappers/todos.ts';
export { createDemoFixture, demoEndpoints, demoSessionsFromStatus, type FixtureData } from './fixtures.ts';
