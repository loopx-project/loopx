import { IconClose } from './icons';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { t } from '../i18n';

interface Props {
  title: string;
  message?: string;
  /** When set, the dialog asks for text. The value is passed to onConfirm. */
  input?: { initial: string; label: string; secret?: boolean };
  /** Optional checkboxes, off by default. Their states, in order, are the second argument to onConfirm. */
  options?: Array<{ label: string; hint?: string }>;
  confirmLabel: string;
  danger?: boolean;
  /** A dialog that only reports something: no second Cancel next to the one action. */
  hideCancel?: boolean;
  children?: ReactNode;
  confirmDisabled?: boolean;
  className?: string;
  onConfirm: (value: string | undefined, checked: boolean[]) => void;
  onCancel: () => void;
}

/** In-app modal used instead of window.prompt / window.confirm, so it matches the rest of the UI. */
export function Dialog({ title, message, input, options = [], confirmLabel, danger, hideCancel = false, children, confirmDisabled, className = '', onConfirm, onCancel }: Props) {
  const [value, setValue] = useState(input?.initial ?? '');
  const [checked, setChecked] = useState<boolean[]>(() => options.map(() => false));
  const inputRef = useRef<HTMLInputElement>(null);
  // Only a click that both starts and ends outside counts: a text selection dragged past the edge
  // ends on the scrim too. A dialog with typing in it closes only by its buttons or Escape; a click
  // outside, say to clear the selection, goes back to the field instead of losing the text.
  const pressedOutside = useRef(false);

  useEffect(() => {
    // Only select on opening; streaming updates can replace the callbacks while editing.
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.isComposing && e.keyCode !== 229) onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const submit = () => {
    if (confirmDisabled) return;
    if (input && !value.trim()) return;
    onConfirm(input ? value.trim() : undefined, checked);
  };

  return (
    <div
      className="dialog-scrim"
      onPointerDown={(e) => { pressedOutside.current = e.target === e.currentTarget; }}
      onClick={(e) => {
        if (e.target !== e.currentTarget || !pressedOutside.current) return;
        if (input) inputRef.current?.focus();
        else onCancel();
      }}
    >
      <div
        className={`dialog ${className}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="dialog-title" className="dialog-title">
          {title}
        </h2>
        {message && <p className="dialog-message">{message}</p>}
        {children}
        {input && (
          <label className="dialog-field">
            <span className="sr-only">{input.label}</span>
            <input
              ref={inputRef}
              value={value}
              type={input.secret ? 'password' : undefined}
              autoComplete={input.secret ? 'off' : undefined}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submit();
                }
              }}
              aria-label={input.label}
            />
          </label>
        )}
        {options.map((option, i) => (
          <label key={option.label} className="dialog-option">
            <input type="checkbox" checked={checked[i] ?? false} onChange={(e) => setChecked((prev) => options.map((_, j) => (j === i ? e.target.checked : (prev[j] ?? false))))} />
            <span className="dialog-option-text">
              <span>{option.label}</span>
              {option.hint && <span className="dialog-option-hint">{option.hint}</span>}
            </span>
          </label>
        ))}
        <div className="dialog-actions">
          {!hideCancel && (
            <button className="ghost" onClick={onCancel}>
              {t('common.cancel')}
            </button>
          )}
          <button className={danger ? 'danger' : 'primary'} onClick={submit} disabled={confirmDisabled || (!!input && !value.trim())}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

interface ChoiceProps {
  title: string;
  choices: Array<{ id: string; icon: ReactNode; label: string; hint: string }>;
  onPick: (id: string) => void;
  onCancel: () => void;
}

/** A dialog that asks which way to go: each answer a row with a word on what it means. Picking one is the confirmation. */
export function ChoiceDialog({ title, choices, onPick, onCancel }: ChoiceProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.isComposing && e.keyCode !== 229) onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  return (
    <div className="dialog-scrim" onClick={onCancel}>
      <div className="dialog choice-dialog" role="dialog" aria-modal="true" aria-labelledby="choice-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="choice-title" className="dialog-title">
          {title}
        </h2>
        <div className="choice-list">
          {choices.map((c, i) => (
            <button key={c.id} type="button" className="choice-row" autoFocus={i === 0} onClick={() => onPick(c.id)}>
              <span className="choice-icon">{c.icon}</span>
              <span className="choice-text">
                <span className="choice-label">{c.label}</span>
                <span className="choice-hint">{c.hint}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Shared modal surface for dialogs with custom content. */
export function DialogSurface({ children, className = '', labelledBy, onClose, busy = false }: {
  children: ReactNode;
  className?: string;
  labelledBy: string;
  onClose: () => void;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef({ onClose, busy });
  closeRef.current = { onClose, busy };
  const pressedOutside = useRef(false);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || e.keyCode === 229) return;
      const surface = ref.current;
      // A nested dialog or a portalled menu owns the keyboard until it closes. Listening on the
      // document also handles a focused button becoming disabled while its request is in flight.
      const dialogs = [...document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]')];
      if (!surface || dialogs.at(-1) !== surface || document.querySelector('.pop-panel')) return;
      const owner = e.target instanceof Element ? e.target.closest('[role="dialog"]') : null;
      if (owner && owner !== surface) return;
      if (e.key === 'Escape') { e.stopPropagation(); if (!closeRef.current.busy) closeRef.current.onClose(); }
      if (e.key !== 'Tab') return;
      const items = [...surface.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]')].filter(el => el.getClientRects().length > 0);
      const first = items[0];
      const last = items[items.length - 1];
      if (!first) { e.preventDefault(); surface.focus(); return; }
      const outside = !surface.contains(document.activeElement) || document.activeElement === surface;
      if (e.shiftKey && (document.activeElement === first || outside)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || outside)) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus({ preventScroll: true }); };
  }, []);
  return (
    <div className="dialog-scrim" onPointerDown={e => { pressedOutside.current = e.target === e.currentTarget; }} onClick={e => { if (!busy && pressedOutside.current && e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} tabIndex={-1} className={`dialog ${className}`} role="dialog" aria-modal="true" aria-labelledby={labelledBy}
        onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}

/** Read-only information dialog, sharing the app's modal treatment without confirmation actions. */
export function PanelDialog({ title, children, onClose, className = '' }: { title: string; children: ReactNode; onClose: () => void; className?: string }) {
  const close = useRef<HTMLButtonElement>(null);
  const closeAction = useRef(onClose);
  closeAction.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape' && !event.isComposing && event.keyCode !== 229) closeAction.current(); };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); previous?.focus(); };
  }, []);
  return <div className="dialog-scrim" onClick={onClose}>
    <section className={`panel ${className}`} role="dialog" aria-modal="true" aria-label={title} onClick={event => event.stopPropagation()}>
      <header className="panel-head"><h2 className="panel-title">{title}</h2>
        <button ref={close} className="icon-btn" aria-label={t('common.close')} onClick={onClose}><IconClose size={18} /></button>
      </header>{children}
    </section>
  </div>;
}
