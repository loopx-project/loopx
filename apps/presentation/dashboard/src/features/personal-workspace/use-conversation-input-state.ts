import { useEffect, useRef, useState, type SetStateAction } from "react";
import type { WorkspaceImageAttachment } from "./personal-workspace-model";

interface ConversationInputState {
  composer: string;
  sending: boolean;
  steering: boolean;
  actionFeedback: string | null;
  imageAttachments: WorkspaceImageAttachment[];
  imageAttachmentError: string | null;
  loopxMessageReceipt: string;
}

const emptyInput: ConversationInputState = {
  composer: "",
  sending: false,
  steering: false,
  actionFeedback: null,
  imageAttachments: [],
  imageAttachmentError: null,
  loopxMessageReceipt: "",
};

// Original conversations have one fixed Session. Executor discovery is a read,
// so its loading/unavailable placeholder must not select another draft.
export function conversationInputKey(conversationKey: string, agentId: string, fixedSession: boolean): string {
  return fixedSession ? conversationKey : `${conversationKey}:${agentId}`;
}

function retainedInputs(inputs: Record<string, ConversationInputState>, key: string) {
  return Object.entries(inputs).filter(([candidate, input]) => candidate.startsWith(`${key}:`)
    && Boolean(input.composer || input.imageAttachments.length));
}

export function recoverBoundConversationInput(inputs: Record<string, ConversationInputState>, key: string) {
  if (inputs[key]) return inputs;
  const retained = retainedInputs(inputs, key);
  // Multiple old drafts are distinct user input. Keep them available for explicit
  // recovery instead of guessing which one to overwrite or concatenating them.
  if (retained.length !== 1) return inputs;
  const [previousKey, input] = retained[0];
  const recovered = { ...inputs, [key]: input };
  delete recovered[previousKey];
  return recovered;
}

// An async callback keeps the key from its originating render. Leaving a
// conversation does not cancel delivery or let its late receipt edit a peer.
// This state is presentation only; Session/Turn ingress still owns effects.
export function useConversationInputState(key: string, recoverBoundDrafts = false) {
  const [inputs, setInputs] = useState<Record<string, ConversationInputState>>(() => {
    try {
      const parsed = JSON.parse(window.sessionStorage.getItem("loopx-pw-composer-drafts") ?? "{}");
      const restored = parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? Object.fromEntries(Object.entries(parsed).filter(([, text]) => typeof text === "string")
          .map(([draftKey, text]) => [draftKey, { ...emptyInput, composer: text as string }]))
        : {};
      return recoverBoundDrafts ? recoverBoundConversationInput(restored, key) : restored;
    } catch { return {}; }
  });
  useEffect(() => {
    if (recoverBoundDrafts) setInputs(current => recoverBoundConversationInput(current, key));
  }, [key, recoverBoundDrafts]);
  const savedDrafts = JSON.stringify(Object.fromEntries(Object.entries(inputs)
    .filter(([, input]) => input.composer).map(([draftKey, input]) => [draftKey, input.composer])));
  useEffect(() => {
    try { window.sessionStorage.setItem("loopx-pw-composer-drafts", savedDrafts); }
    catch { /* Storage may be unavailable; input remains in memory. */ }
  }, [savedDrafts]);
  const visibleKey = useRef(key);
  visibleKey.current = key;

  function updateInput(update: (input: ConversationInputState) => ConversationInputState) {
    setInputs((current) => {
      const input = current[key] ?? emptyInput;
      const next = update(input);
      return next === input ? current : { ...current, [key]: next };
    });
  }

  function setter<K extends keyof ConversationInputState>(field: K) {
    return (value: SetStateAction<ConversationInputState[K]>) => updateInput((input) => {
      const next = typeof value === "function"
        ? (value as (previous: ConversationInputState[K]) => ConversationInputState[K])(input[field])
        : value;
      return { ...input, [field]: next };
    });
  }

  return {
    ...(inputs[key] ?? emptyInput),
    setComposer: (value: string, expectedValue?: string) => updateInput((input) =>
      expectedValue !== undefined && input.composer !== expectedValue ? input : { ...input, composer: value }),
    restoreFailedSubmission: (text: string, images: WorkspaceImageAttachment[]) => updateInput((input) =>
      input.composer || input.imageAttachments.length ? input : { ...input, composer: text, imageAttachments: images }),
    setSending: setter("sending"),
    setSteering: setter("steering"),
    setActionFeedback: setter("actionFeedback"),
    setImageAttachments: setter("imageAttachments"),
    setImageAttachmentError: setter("imageAttachmentError"),
    setLoopxMessageReceipt: setter("loopxMessageReceipt"),
    isCurrentConversation: () => visibleKey.current === key,
    retainedDrafts: recoverBoundDrafts ? retainedInputs(inputs, key) : [],
    restoreRetainedDraft: (previousKey: string) => setInputs(current => {
      const input = current[key] ?? emptyInput;
      const previous = recoverBoundDrafts && retainedInputs(current, key).find(([candidate]) => candidate === previousKey)?.[1];
      if (!previous || input.composer || input.imageAttachments.length || input.sending || input.steering) return current;
      const restored = { ...current, [key]: { ...input, composer: previous.composer, imageAttachments: previous.imageAttachments } };
      delete restored[previousKey];
      return restored;
    }),
  };
}
