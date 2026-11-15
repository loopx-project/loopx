import { useState, type ReactNode } from 'react';
import type { Thread } from '../api';
import { copyText } from '../clipboard';
import { t } from '../i18n';
import { folderName } from '../paths';
import { IconCheck, IconCopy, IconFolder } from './icons';
import { Popover } from './Popover';

interface Props {
  thread: Thread;
  agent: string;
  /** The machine, when it is not the hub's own. */
  host?: string;
  /** On a phone the title itself opens the details; there is no tag beside it. */
  title?: ReactNode;
}

/**
 * Where a thread runs, on demand, as in Codex: a quiet folder icon before the title, naming the
 * project (and the branch of a worktree, and the machine when it is another one) on hover, and
 * opening the full path, agent and the command to continue in a terminal, each one copyable.
 */
export function ThreadPlace({ thread, agent, host, title }: Props) {
  const project = thread.scratch ? t('projects.none') : folderName(thread.worktree?.of ?? thread.cwd);
  const rows: Array<{ id: string; label: string; value: string; mono?: boolean }> = [
    { id: 'dir', label: t('place.directory'), value: thread.cwdReal || thread.cwd, mono: true },
    ...(thread.worktree ? [{ id: 'branch', label: t('place.branch'), value: thread.worktree.branch, mono: true }] : []),
    ...(host ? [{ id: 'host', label: t('place.machine'), value: host }] : []),
    { id: 'agent', label: t('place.agent'), value: agent },
    ...(thread.resumeCommand ? [{ id: 'resume', label: t('place.resume'), value: thread.resumeCommand, mono: true }] : []),
  ];
  return (
    <Popover
      label={t('place.details')}
      placement="below"
      align="start"
      triggerClassName={title ? 'topbar-title topbar-title-btn' : 'icon-btn place-icon'}
      triggerTitle={title ? undefined : [project, thread.worktree?.branch, host].filter(Boolean).join(' · ')}
      triggerLabel={title ? undefined : t('place.details')}
      panelClassName="place-panel"
      trigger={title ?? <IconFolder size={16} />}
    >
      {() => rows.map(row => <PlaceRow key={row.id} {...row} />)}
    </Popover>
  );
}

function PlaceRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="place-row">
      <span className="place-label">{label}</span>
      <span className={`place-value${mono ? ' mono' : ''}`}>{value}</span>
      <button
        type="button"
        className="icon-btn place-copy"
        role="menuitem"
        aria-label={`${t('common.copy')} ${label}`}
        title={copied ? t('common.copied') : t('common.copy')}
        onClick={() => void copyText(value).then(ok => {
          if (!ok) return;
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        })}
      >
        {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
      </button>
    </div>
  );
}
