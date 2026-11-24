import { StewardConversation } from './StewardConversation';
import type { Api, StewardLedgerItem } from '../api';
import type { LoopxStatusFailure } from '../loopx/status';

/** The steward stays chat-first; the app owns its shared top-right on-demand overview and side peek. */
export function StewardPage({
  token,
  failure,
  deferSessionOpen = false,
  api,
  ledgerCapture = true,
  onPeekSession,
}: {
  token: string | undefined;
  failure: LoopxStatusFailure | null;
  deferSessionOpen?: boolean;
  api?: Api;
  ledgerCapture?: boolean;
  onPeekSession?: (target: StewardLedgerItem['jumpTarget'], title?: string) => void;
}) {
  return (
    <div className="steward-page is-chat">
      <StewardConversation
        token={token}
        failure={failure}
        deferSessionOpen={deferSessionOpen}
        api={api}
        ledgerCapture={ledgerCapture}
        onPeekSession={onPeekSession}
      />
    </div>
  );
}
