import { StewardConversation } from './StewardConversation';
import type { LoopxStatusFailure } from '../loopx/status';

/** The steward stays chat-first; the app owns its shared top-right overview. */
export function StewardPage({ token, failure }: { token: string | undefined; failure: LoopxStatusFailure | null }) {
  return <div className="steward-page is-chat">
    <StewardConversation token={token} failure={failure} />
  </div>;
}
