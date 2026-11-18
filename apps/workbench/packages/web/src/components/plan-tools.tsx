import { useEffect, useState } from 'react';
import { copyText } from '../clipboard';
import { t } from '../i18n';
import { downloadBlob } from '../images';
import { IconCopy, IconDownload, IconCheck } from './icons';

/** The file a plan is saved as: its first heading, or a plain generic name. */
export function planFileName(text: string): string {
  const heading = text
    .split('\n')
    .map((line) => line.trim())
    .find((line) => /^#{1,6}\s+/.test(line));
  const name = heading ? heading.replace(/^#{1,6}\s+/, '').replace(/[*\\/:?"<>|]/g, '').trim() : '';
  return `${name || t('plan.defaultFileName')}.md`;
}

/** Saves the plan as a Markdown file. */
export function PlanDownloadButton({ text, className = 'icon-btn' }: { text: string; className?: string }) {
  return (
    <button
      type="button"
      className={className}
      aria-label={t('plan.download')}
      title={t('plan.download')}
      onClick={(e) => {
        e.stopPropagation();
        downloadBlob(new Blob([text], { type: 'text/markdown' }), planFileName(text));
      }}
    >
      <IconDownload size={16} />
    </button>
  );
}

/** Copies the plan, saying so the way every copy button here does. */
export function PlanCopyButton({ text, className = 'icon-btn' }: { text: string; className?: string }) {
  const [state, setState] = useState<'idle' | 'done' | 'failed'>('idle');
  useEffect(() => {
    if (state === 'idle') return;
    const timer = setTimeout(() => setState('idle'), 1600);
    return () => clearTimeout(timer);
  }, [state]);
  return (
    <button
      type="button"
      className={className}
      aria-label={t('plan.copy')}
      title={state === 'failed' ? t('plan.copyFailed') : state === 'done' ? t('plan.copyDone') : t('plan.copy')}
      onClick={async (e) => {
        e.stopPropagation();
        setState((await copyText(text)) ? 'done' : 'failed');
      }}
    >
      {state === 'done' ? <IconCheck size={16} /> : <IconCopy size={16} />}
    </button>
  );
}
