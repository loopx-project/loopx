import { useEffect } from 'react';
import type { WritingStage, WritingState } from '../api';
import { useIsNarrow } from '../hooks';
import { t } from '../i18n';
import { bodyName, stageLabel, WRITING_STAGES } from '../writing';
import { IconCheck, IconChevron, IconEdit } from './icons';
import { Popover } from './Popover';

interface Props {
  writing: WritingState;
  /** A change is on its way to the daemon: the controls wait for it. */
  busy: boolean;
  onStage: (stage: WritingStage) => void;
  onEnd: () => void;
  onOpen: (file: string) => void;
  /** The person's writing habits note, if this daemon says where it is: its path, and whether it
   * has been written yet (null while that is being looked for). */
  habits?: { path: string; ready: boolean | null } | null;
  /** Asked each time the popover opens: the note may have been written by the last turn. */
  onCheckHabits?: () => void;
  onOpenHabits?: (path: string) => void;
}

/**
 * Writing mode's one line above the input: a quiet chip naming the stage. Everything else — the
 * stages, the body, the way out — lives behind it in one popover, the same on a phone as here.
 */
export function WritingBar({ writing, busy, onStage, onEnd, onOpen, habits = null, onCheckHabits, onOpenHabits }: Props) {
  const narrow = useIsNarrow();
  const name = writing.file ? bodyName(writing.file) : null;

  return (
    <div className="writing-bar" role="group" aria-label={t('writing.mode')}>
      <Popover
        label={t('writing.stages')}
        triggerClassName="writing-chip"
        triggerTitle={t('writing.stages')}
        triggerLabel={`${t('writing.label')} · ${stageLabel(writing.stage)}`}
        disabled={busy}
        trigger={
          <>
            <IconEdit size={13} />
            <span>{t('writing.label')}</span>
            <span className="writing-chip-dot" aria-hidden="true">·</span>
            <span className="writing-chip-stage">{stageLabel(writing.stage)}</span>
            <span className="writing-chip-caret" aria-hidden="true"><IconChevron size={12} open /></span>
          </>
        }
      >
        {close => (
          <>
            {/* Looked for every time the menu opens: the note may have appeared with a turn. */}
            {habits && onCheckHabits && <OnOpen onOpen={onCheckHabits} />}
            {WRITING_STAGES.map(stage => (
              <button
                type="button"
                key={stage}
                role="menuitemradio"
                aria-checked={stage === writing.stage}
                className="pop-item"
                onClick={() => {
                  close();
                  if (stage !== writing.stage) onStage(stage);
                }}
              >
                <span className="pop-item-text">
                  <span className="pop-item-label">{stageLabel(stage)}</span>
                  <span className="pop-item-hint">{t(`writing.stageHint.${stage}`)}</span>
                </span>
                {stage === writing.stage && (
                  <span className="pop-item-check"><IconCheck size={16} /></span>
                )}
              </button>
            ))}
            <div className="pop-divider" />
            {writing.file && name ? (
              <button
                type="button"
                role="menuitem"
                className="pop-item"
                title={writing.file}
                onClick={() => {
                  close();
                  onOpen(writing.file!);
                }}
              >
                <span className="pop-item-text">
                  <span className="pop-item-label">{t('writing.openBodyLabel')}</span>
                  <span className="pop-item-hint">{name}</span>
                </span>
              </button>
            ) : (
              <div className="pop-item writing-pop-nobody">
                <span className="pop-item-text">
                  <span className="pop-item-label">{t('writing.openBodyLabel')}</span>
                  <span className="pop-item-hint">{t('writing.noBodyHint')}</span>
                </span>
              </div>
            )}
            <div className="pop-divider" />
            {habits && (habits.ready ? (
              <button
                type="button"
                role="menuitem"
                className="pop-item"
                title={habits.path}
                onClick={() => {
                  close();
                  onOpenHabits?.(habits.path);
                }}
              >
                <span className="pop-item-text">
                  <span className="pop-item-label">{t('writing.habits')}</span>
                  <span className="pop-item-hint">{habits.path.split('/').pop() ?? habits.path}</span>
                </span>
              </button>
            ) : (
              <div className="pop-item writing-pop-nobody">
                <span className="pop-item-text">
                  <span className="pop-item-label">{t('writing.habits')}</span>
                  <span className="pop-item-hint">{t('writing.habitsHint')}</span>
                </span>
              </div>
            ))}
            <div className="pop-divider" />
            <button type="button" role="menuitem" className="pop-item" title={t('writing.off')} onClick={() => { close(); onEnd(); }}>
              <span className="pop-item-text"><span className="pop-item-label">{t('writing.endFull')}</span></span>
            </button>
          </>
        )}
      </Popover>
      {/* On a phone the body is one tap from the chip itself; the popover has it too. */}
      {narrow && writing.file && name && (
        <button
          type="button"
          className="writing-open-link"
          title={writing.file}
          aria-label={t('writing.openBody', name)}
          onClick={() => onOpen(writing.file!)}
        >
          {t('writing.openBodyLabel')}
        </button>
      )}
    </div>
  );
}

/** Fires once when the popover opens, so whatever it needs to know is looked for then. */
function OnOpen({ onOpen }: { onOpen: () => void }) {
  useEffect(() => {
    onOpen();
  }, [onOpen]);
  return null;
}
