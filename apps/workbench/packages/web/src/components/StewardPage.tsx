import { StewardConversation } from './StewardConversation';
import type { LoopxStatusFailure } from '../loopx/status';

/** The steward stays chat-first; the app owns its shared top-right overview. */
export function StewardPage({ token, failure, deferSessionOpen = false }: { token: string | undefined; failure: LoopxStatusFailure | null; deferSessionOpen?: boolean }) {
  return <div className="steward-page is-chat">
    <StewardConversation token={token} failure={failure} deferSessionOpen={deferSessionOpen} />
  </div>;
}
