import { t } from '../i18n';
import './ConversationSkeleton.css';

interface Props {
  /** Narrower preview drawer layout (ThreadPeek) uses a single-turn compact placeholder. */
  compact?: boolean;
  className?: string;
}

/**
 * Conversation loading skeleton matching Calli's real layout geometry (Codex-style):
 * right-aligned user bubble, collapsed work summary row, and borderless assistant prose lines,
 * anchored toward the composer so incoming messages settle without jarring visual jump.
 */
export function ConversationSkeleton({ compact = false, className = '' }: Props) {
  return (
    <div
      className={`chat-skeleton${compact ? ' compact' : ''}${className ? ` ${className}` : ''}`}
      role="status"
      aria-label={t('common.loading')}
    >
      {!compact && (
        <div className="chat-skeleton-turn prior" aria-hidden="true">
          <div className="chat-skeleton-user wide">
            <span className="chat-skeleton-user-line l1" />
            <span className="chat-skeleton-user-line l2" />
          </div>
          <div className="chat-skeleton-work" />
          <div className="chat-skeleton-prose">
            <span className="chat-skeleton-line w94" />
            <span className="chat-skeleton-line w86" />
            <span className="chat-skeleton-line w62" />
          </div>
        </div>
      )}
      <div className="chat-skeleton-turn" aria-hidden="true">
        <div className="chat-skeleton-user">
          <span className="chat-skeleton-user-line l1" />
          {compact && <span className="chat-skeleton-user-line l2" />}
        </div>
        {compact && <div className="chat-skeleton-work" />}
        <div className="chat-skeleton-prose">
          <span className="chat-skeleton-line w90" />
          <span className="chat-skeleton-line w74" />
          {compact && <span className="chat-skeleton-line w62" />}
        </div>
      </div>
    </div>
  );
}
