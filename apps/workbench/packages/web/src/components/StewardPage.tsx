import { StewardConversation, type StewardWaitingItem } from './StewardConversation';
import type { LoopxStatusFailure } from '../loopx/status';

/** The steward stays chat-first; the app owns its shared top-right overview. */
export function StewardPage({
  token,
  failure,
  deferSessionOpen = false,
  waitingGoals,
  onOpenGoal,
  onOpenThread,
  onOpenGoalsOverview,
}: {
  token: string | undefined;
  failure: LoopxStatusFailure | null;
  deferSessionOpen?: boolean;
  waitingGoals?: StewardWaitingItem[];
  onOpenGoal?: (goalId: string) => void;
  onOpenThread?: (threadId: string) => void;
  onOpenGoalsOverview?: () => void;
}) {
  return <div className="steward-page is-chat">
    <StewardConversation
      token={token}
      failure={failure}
      deferSessionOpen={deferSessionOpen}
      waitingGoals={waitingGoals}
      onOpenGoal={onOpenGoal}
      onOpenThread={onOpenThread}
      onOpenGoalsOverview={onOpenGoalsOverview}
    />
  </div>;
}
