import { useEffect } from 'react';

export interface ToastState {
  message: string;
  /** An optional follow-up, such as undoing what was just done. */
  action?: { label: string; run: () => void };
  /** Something is still in progress: the toast stays until it is replaced or cleared. */
  busy?: boolean;
  /** Waits for the user: it stays until acted on or replaced. */
  sticky?: boolean;
}

interface Props {
  toast: ToastState | null;
  onDismiss: () => void;
}

/** A short confirmation at the bottom of the screen that goes away on its own. */
export function Toast({ toast, onDismiss }: Props) {
  useEffect(() => {
    if (!toast || toast.busy || toast.sticky) return;
    // Longer when there is something to act on.
    const timer = setTimeout(onDismiss, toast.action ? 6000 : 2500);
    return () => clearTimeout(timer);
  }, [toast, onDismiss]);

  if (!toast) return null;
  return (
    <div className="toast" role="status">
      <span>{toast.message}</span>
      {toast.action && (
        <button
          type="button"
          className="toast-action"
          onClick={() => {
            toast.action?.run();
            onDismiss();
          }}
        >
          {toast.action.label}
        </button>
      )}
    </div>
  );
}
