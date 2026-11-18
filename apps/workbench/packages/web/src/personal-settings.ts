import type { PersonalSettings } from './api';

type Choice = Pick<PersonalSettings, 'agent' | 'model' | 'directory'>;

/** A model belongs to its agent, so it goes only when the agent really changes. */
export const pickAgent = <T extends Choice>(value: T, agent: string | null): T =>
  agent === value.agent ? value : { ...value, agent, model: null };

/** Whether saving this moves the assistant's next message to a new conversation. */
export const startsNewConversation = (initial: PersonalSettings, value: Choice): boolean =>
  Boolean(initial.routerThreadId) && (value.agent !== initial.agent || value.model !== initial.model || value.directory !== initial.directory);
