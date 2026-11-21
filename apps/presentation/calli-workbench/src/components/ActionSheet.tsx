import { useState, type ReactNode } from 'react';
import { t } from '../i18n';
import type { UserInputAnswer, UserQuestion } from '../api';
import { shortenPaths } from '../paths';
import type { Item } from '../timeline';
import { DiffView, optionLabel, Stats } from './ThreadView';
import { IconArrowLeft, IconArrowUp, IconCheck, IconChevron, IconEdit } from './icons';

/** What the sheet is asking about right now: a question, a plan decision, or a tool approval. */
export type SheetTarget =
  | { kind: 'question'; item: Extract<Item, { kind: 'input' }> }
  | { kind: 'plan-approval'; item: Extract<Item, { kind: 'input' }> }
  | { kind: 'hub-plan'; item: Extract<Item, { kind: 'proposal' }> }
  | { kind: 'approval'; item: Extract<Item, { kind: 'approval' }> };

interface Props {
  target: SheetTarget;
  /** Which of the things waiting for the user this is, when there are several. */
  index: number;
  total: number;
  /** Every spelling of the thread's directory, so paths can be shown relative to it. */
  roots: string[];
  onAnswer: (id: string, answer: UserInputAnswer) => Promise<void> | void;
  onDecide: (approvalId: string, optionId: string | null) => void;
  /** Goes ahead with the plan: sends the go-ahead message, in execute mode. Missing while a turn runs. */
  onExecutePlan?: () => Promise<void> | void;
  /** Sends the plan back with what must change, putting the thread in plan mode again. */
  onRejectPlan: (text: string) => Promise<void> | void;
  /** Leaves the plan be: the sheet closes and stays closed for this plan. */
  onSkipPlan: () => void;
}

/** One thing to pick: a name, a line under it saying more, and a mark at the right when picked. */
function OptionRow({ name, description, checked, emphasised, disabled, title, role, ariaChecked, trailing, onClick }: {
  name: string;
  description?: string;
  /** Given (even false) only where a mark belongs: a choice that can sit beside others. */
  checked?: boolean;
  emphasised?: boolean;
  disabled?: boolean;
  title?: string;
  role?: 'radio' | 'checkbox';
  ariaChecked?: boolean;
  trailing?: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role={role}
      aria-checked={ariaChecked}
      title={title}
      disabled={disabled}
      className={`sheet-option${emphasised ? ' emphasised' : ''}`}
      onClick={onClick}
    >
      <span className="sheet-option-text">
        <span className="sheet-option-name">{name}</span>
        {description && <span className="sheet-option-desc">{description}</span>}
      </span>
      {trailing !== undefined
        ? trailing
        : checked !== undefined && (
            <span className={`sheet-check${checked ? ' on' : ''}`} aria-hidden="true">
              {checked && <IconCheck size={13} />}
            </span>
          )}
    </button>
  );
}

/** The shell every kind of question is asked in: a label, a title, and the body under them. */
function Sheet({ ariaLabel, label, count, back, title, children }: {
  ariaLabel: string;
  label: string;
  count?: string;
  back?: () => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="action-sheet">
      <section className="action-sheet-card" role="dialog" aria-label={ariaLabel}>
        <div className="sheet-head">
          {back && (
            <button type="button" className="icon-btn sheet-back" aria-label={t('sheet.back')} title={t('sheet.back')} onClick={back}>
              <IconArrowLeft size={16} />
            </button>
          )}
          <span className="sheet-label">{label}</span>
          {count && <span className="sheet-count">{count}</span>}
        </div>
        <div className="sheet-title">{title}</div>
        {children}
      </section>
    </div>
  );
}

/** The line to write in instead of picking an option, and what sits at its right. */
function SheetInput({ placeholder, value, onChange, onSend, right }: {
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  right?: ReactNode;
}) {
  return (
    <div className="sheet-bottom">
      <IconEdit size={16} />
      <input
        className="sheet-input"
        value={value}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
            e.preventDefault();
            onSend();
          }
        }}
      />
      {value.trim() ? (
        <button type="button" className="sheet-send" aria-label={t('sheet.send')} title={t('sheet.send')} onClick={onSend}>
          <IconArrowUp size={16} />
        </button>
      ) : (
        right
      )}
    </div>
  );
}

/** Where a submitted answer went wrong, said where the answer was given. */
function SheetError({ error }: { error: string }) {
  return <div className="sheet-error" role="alert">{error}</div>;
}

/** The question the agent asked, one question at a time. */
function QuestionSheet({ item, index, total, onAnswer }: {
  item: Extract<Item, { kind: 'input' }>;
  index: number;
  total: number;
  onAnswer: Props['onAnswer'];
}) {
  const questions = item.questions;
  const [qi, setQi] = useState(0);
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [custom, setCustom] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const q: UserQuestion | undefined = questions[qi];
  const last = qi >= questions.length - 1;

  const values = Object.fromEntries(questions.map((question) => [
    question.id,
    custom[question.id]?.trim()
      ? question.multiple
        ? [...(picked[question.id] ?? []), custom[question.id].trim()]
        : [custom[question.id].trim()]
      : picked[question.id] ?? [],
  ]));
  const ready = questions.every((question) => question.required === false || values[question.id].length);

  const submit = async (answer: UserInputAnswer) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await onAnswer(item.inputId, answer);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const count = questions.length > 1 ? `${qi + 1}/${questions.length}` : total > 1 ? `${index}/${total}` : undefined;

  if (!q) {
    return (
      <Sheet ariaLabel={t('sheet.question.aria')} label={item.title || t('sheet.question.label')} count={count} title={item.title || t('sheet.question.defaultTitle')}>
        <div className="sheet-bottom">
          <span className="sheet-note" />
          <button type="button" className="primary" disabled={busy} onClick={() => void submit({ answers: {} })}>
            {t('sheet.submit')}
          </button>
        </div>
        {error && <SheetError error={error} />}
      </Sheet>
    );
  }

  const pick = (value: string) => {
    setPicked((prev) => ({
      ...prev,
      [q.id]: q.multiple
        ? (prev[q.id] ?? []).includes(value)
          ? (prev[q.id] ?? []).filter((v) => v !== value)
          : [...(prev[q.id] ?? []), value]
        : [value],
    }));
    if (q.multiple) return;
    setCustom((prev) => ({ ...prev, [q.id]: '' }));
    // One choice settles a single-choice question: the next one is asked, or all is sent.
    if (!last) {
      setQi(qi + 1);
      return;
    }
    const withThis = { ...values, [q.id]: [value] };
    if (questions.every((question) => question.required === false || withThis[question.id].length)) {
      void submit({ answers: withThis });
    }
  };

  const typed = custom[q.id] ?? '';
  const answeredHere = (values[q.id] ?? []).length > 0;
  // Writing instead of picking answers this question; on the last one it sends everything.
  const sendTyped = () => {
    if (!typed.trim()) return;
    if (!last) setQi(qi + 1);
    else if (ready) void submit({ answers: values });
  };
  const skip = (
    <button type="button" className="ghost sheet-skip" disabled={busy} onClick={() => void submit({ cancelled: true })}>
      {t('sheet.skip')}
    </button>
  );

  return (
    <Sheet
      ariaLabel={t('sheet.question.aria')}
      label={q.header || item.title || t('sheet.question.label')}
      count={count}
      back={qi > 0 ? () => setQi(qi - 1) : undefined}
      title={q.question}
    >
      <div className="sheet-options" role={q.multiple ? 'group' : 'radiogroup'} aria-label={q.question}>
        {q.options.map((o) => (
          <OptionRow
            key={o.value}
            name={o.label}
            description={o.description}
            role={q.multiple ? 'checkbox' : 'radio'}
            ariaChecked={q.multiple ? (picked[q.id] ?? []).includes(o.value) : (picked[q.id] ?? [])[0] === o.value && !typed.trim()}
            checked={q.multiple ? (picked[q.id] ?? []).includes(o.value) : undefined}
            onClick={() => pick(o.value)}
          />
        ))}
      </div>
      {(q.multiple || !q.options.length) && (
        <div className="sheet-next">
          <button
            type="button"
            className="primary"
            disabled={busy || (last ? !ready : !answeredHere)}
            onClick={() => (last ? void submit({ answers: values }) : setQi(qi + 1))}
          >
            {last ? t('sheet.submit') : t('sheet.next')}
          </button>
        </div>
      )}
      {q.freeText ? (
        <SheetInput
          placeholder={q.options.length ? t('sheet.question.otherAnswer') : t('sheet.question.typeAnswer')}
          value={typed}
          onChange={(value) => setCustom((prev) => ({ ...prev, [q.id]: value }))}
          onSend={sendTyped}
          right={skip}
        />
      ) : (
        <div className="sheet-bottom">
          <span className="sheet-note" />
          {skip}
        </div>
      )}
      {error && <SheetError error={error} />}
    </Sheet>
  );
}

/** A plan the agent itself is holding until it is told to go ahead (Claude, zcode). */
function PlanApprovalSheet({ item, onAnswer }: { item: Extract<Item, { kind: 'input' }>; onAnswer: Props['onAnswer'] }) {
  const q = item.questions[0];
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async (answer: UserInputAnswer) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await onAnswer(item.inputId, answer);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <Sheet ariaLabel={t('sheet.plan.aria')} label={t('sheet.plan.label')} title={t('sheet.plan.title')}>
      <div className="sheet-options">
        <OptionRow
          name={t('sheet.plan.confirm')}
          emphasised
          disabled={busy || !q}
          onClick={() => q && void submit({ answers: { [q.id]: [q.options[0].value] } })}
        />
      </div>
      {q?.freeText ? (
        <SheetInput
          placeholder={t('sheet.plan.rejectHint')}
          value={text}
          onChange={setText}
          onSend={() => text.trim() && void submit({ answers: { [q.id]: [text.trim()] } })}
          right={
            <button type="button" className="ghost sheet-skip" disabled={busy} onClick={() => void submit({ cancelled: true })}>
              {t('sheet.skip')}
            </button>
          }
        />
      ) : (
        <div className="sheet-bottom">
          <span className="sheet-note" />
          <button type="button" className="ghost sheet-skip" disabled={busy} onClick={() => void submit({ cancelled: true })}>
            {t('sheet.skip')}
          </button>
        </div>
      )}
      {error && <SheetError error={error} />}
    </Sheet>
  );
}

/** A plan the hub is holding: the thread is already back in execute mode, waiting for the word. */
function HubPlanSheet({ onExecutePlan, onRejectPlan, onSkipPlan }: Pick<Props, 'onExecutePlan' | 'onRejectPlan' | 'onSkipPlan'>) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async (what: () => Promise<void> | void | undefined) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await what();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <Sheet ariaLabel={t('sheet.plan.aria')} label={t('sheet.plan.label')} title={t('sheet.plan.title')}>
      <div className="sheet-options">
        <OptionRow name={t('sheet.plan.confirm')} emphasised disabled={busy || !onExecutePlan} onClick={() => void run(() => onExecutePlan?.())} />
      </div>
      <SheetInput
        placeholder={t('sheet.plan.rejectHint')}
        value={text}
        onChange={setText}
        onSend={() => text.trim() && void run(() => onRejectPlan(text.trim()))}
        right={
          <button type="button" className="ghost sheet-skip" disabled={busy} onClick={onSkipPlan}>
            {t('sheet.skip')}
          </button>
        }
      />
      {error && <SheetError error={error} />}
    </Sheet>
  );
}

/** A command or file change the agent wants to run past the user first. */
function ApprovalSheet({ item, roots, onDecide }: { item: Extract<Item, { kind: 'approval' }>; roots: string[]; onDecide: Props['onDecide'] }) {
  const [showDiff, setShowDiff] = useState(false);
  const reject = item.options.find((o) => o.kind.startsWith('reject'));
  let primaryUsed = false;
  return (
    <Sheet ariaLabel={t('sheet.approval.aria')} label={t('sheet.approval.label')} title={shortenPaths(item.title, roots)}>
      {item.command && <pre className="sheet-command">{item.command}</pre>}
      {item.diffs.length > 0 && (
        <div className="sheet-files">
          {item.diffs.map((diff) => (
            <div key={diff.path} className="sheet-file" title={diff.path}>
              <span className="sheet-file-path">{shortenPaths(diff.path, roots)}</span>
              <Stats added={diff.added} removed={diff.removed} />
            </div>
          ))}
          <button type="button" className="sheet-diff-toggle" aria-expanded={showDiff} onClick={() => setShowDiff((v) => !v)}>
            {t('sheet.approval.viewChanges')}
            <IconChevron size={13} open={showDiff} />
          </button>
          {showDiff && (
            <div className="sheet-diff">
              <DiffView diffs={item.diffs} />
            </div>
          )}
        </div>
      )}
      <div className="sheet-options">
        {item.options.map((o) => {
          const primary = o.kind === 'allow_once' && !primaryUsed;
          if (primary) primaryUsed = true;
          return (
            <OptionRow
              key={o.optionId}
              name={optionLabel(o)}
              // "Always allow" means a different thing each time (this file, this command): the
              // option's own wording says which, without repeating what the row is called.
              description={o.kind === 'allow_always' && o.name.startsWith(t('sheet.approval.allowPrefix'))
                ? o.name.slice(t('sheet.approval.allowPrefix').length)
                : o.kind === 'allow_always' ? o.name : undefined}
              emphasised={primary}
              onClick={() => onDecide(item.approvalId, o.optionId)}
            />
          );
        })}
      </div>
      <div className="sheet-bottom">
        <span className="sheet-note" />
        <button
          type="button"
          className="ghost sheet-skip"
          onClick={() => onDecide(item.approvalId, reject ? reject.optionId : null)}
        >
          {t('sheet.approval.reject')}
        </button>
      </div>
    </Sheet>
  );
}

/**
 * Everything that needs the user's word, asked in one place: the bottom sheet that takes the
 * composer's spot while it waits. Questions arrive as `input` events, tool approvals as
 * `approval` events, and a finished plan the hub itself asks whether to go ahead with.
 */
export function ActionSheet(props: Props) {
  const { target } = props;
  if (target.kind === 'question') return <QuestionSheet item={target.item} index={props.index} total={props.total} onAnswer={props.onAnswer} />;
  if (target.kind === 'plan-approval') return <PlanApprovalSheet item={target.item} onAnswer={props.onAnswer} />;
  if (target.kind === 'hub-plan') return <HubPlanSheet onExecutePlan={props.onExecutePlan} onRejectPlan={props.onRejectPlan} onSkipPlan={props.onSkipPlan} />;
  return <ApprovalSheet item={target.item} roots={props.roots} onDecide={props.onDecide} />;
}
