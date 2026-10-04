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

// An async callback keeps the key from its originating render. Leaving a
// conversation does not cancel delivery or let its late receipt edit a peer.
// This state is presentation only; Session/Turn ingress still owns effects.
export function useConversationInputState(key: string) {
  const [inputs, setInputs] = useState<Record<string, ConversationInputState>>(() => {
    try {
      const parsed = JSON.parse(window.sessionStorage.getItem("loopx-pw-composer-drafts") ?? "{}");
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? Object.fromEntries(Object.entries(parsed).filter(([, text]) => typeof text === "string")
          .map(([draftKey, text]) => [draftKey, { ...emptyInput, composer: text as string }]))
        : {};
    } catch { return {}; }
  });
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
  };
}
