import { AutoHandoverChoice } from './AutoHandover';
import { useEffect, useRef, useState } from 'react';
import { copyText } from '../clipboard';
import type { AgentInfo, Api, Thread } from '../api';
import { AgentPicker } from './Pickers';
import { IconCheck, IconCopy, IconEdit } from './icons';
import { t } from '../i18n';

interface Props {
  api: Api;
  thread: Thread;
  agents: AgentInfo[];
  onOpen: (thread: Thread, draft: string) => void;
  onClose: () => void;
}

/** The handover as it is sent: which machine it came from, then what the hub assembled. */
export function handoverMessage(api: Api, summary: string): string {
  const machine = api.host ? t('handover.remote', api.host) : t('handover.local');
  return `${t('handover.source', machine)}\n\n${summary}`;
}

/** Assemble the source session and progress as soon as the dialog opens; no agent turn is needed. */
export function HandoverDialog({ api, thread, agents, onOpen, onClose }: Props) {
  const [summary, setSummary] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const [target, setTarget] = useState(() => agents.find((a) => a.id === thread.agent && a.available && !a.hidden)?.id ?? agents.find((a) => a.available && !a.hidden)?.id ?? '');
  const [targetModel, setTargetModel] = useState<string | undefined>();
  useEffect(() => {
    if (api.demo) return;
    let active = true;
    void api.autoHandover(thread.id).then(v => { if (active && v.targets[0]) { setTarget(v.targets[0].agent); setTargetModel(v.targets[0].model); } }).catch(() => undefined);
    return () => { active = false; };
  }, [api, thread.id]);
  const [creating, setCreating] = useState(false);
  const creatingRef = useRef(false);

  useEffect(() => {
    let active = true;
    setSummary(null);
    setError('');
    setCopied(false);
    void api.handoverSummary(thread.id).then((result) => {
      if (!active) return;
      if ('summary' in result) {
        setSummary(handoverMessage(api, result.summary));
      } else setError(t('handover.notReady'));
    }).catch((e: unknown) => {
      if (active) setError(e instanceof Error ? e.message : String(e));
    });
    return () => { active = false; };
  }, [api, thread.id, revision]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape' && !creatingRef.current && !document.querySelector('.pop-panel')) onClose(); };
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('keydown', key);
      window.clearTimeout(timer.current);
    };
  }, [onClose]);

  const copy = async () => {
    if (!summary) return;
    if (await copyText(summary)) {
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1500);
    } else setError(t('handover.copyFailed'));
  };

  const create = async () => {
    if (!summary || !target || creatingRef.current) return;
    creatingRef.current = true;
    setCreating(true);
    setError('');
    try {
      const successor = await api.createHandoverThread(thread.id, target, targetModel, 'branch');
      onOpen(successor, summary);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      creatingRef.current = false;
      setCreating(false);
    }
  };

  return (
    <div className="dialog-scrim" onClick={() => { if (!creatingRef.current) onClose(); }}>
      <div className="dialog handover" role="dialog" aria-modal="true" aria-labelledby="handover-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="handover-title" className="dialog-title">{t('handover.title')}</h2>
        <p className="dialog-message">{t('handover.message')}</p>
        <AutoHandoverChoice api={api} thread={thread} />
        <div className="handover-successor">
          <span>{t('handover.successor')}</span>
          <fieldset disabled={creating}>
            <AgentPicker api={api} agents={agents} value={target} onChange={id => { setTarget(id); setTargetModel(undefined); }} />
          </fieldset>
        </div>
        {targetModel && <p className="handover-note">{targetModel}</p>}
        <p className="handover-cwd" title={thread.cwd}>{thread.cwd}</p>
        {summary ? <pre className="handover-summary" tabIndex={0}>{summary}</pre> : !error && <p className="handover-note" role="status">{t('handover.preparing')}</p>}
        {error && <p className="panel-error" role="alert">{error}</p>}
        <div className="dialog-actions">
          {error && !summary && <button className="ghost" onClick={() => setRevision((v) => v + 1)}>{t('common.retry')}</button>}
          <button className="ghost" onClick={onClose} disabled={creating}>{t('common.close')}</button>
          <button className="ghost" onClick={() => void copy()} disabled={!summary}>
            {copied ? <IconCheck size={15} /> : <IconCopy size={15} />} {copied ? t('common.copied') : t('handover.copy')}
          </button>
          <button className="primary" onClick={() => void create()} disabled={!summary || !agents.some((a) => a.id === target && a.available && !a.hidden) || creating}>
            <IconEdit size={15} /> {creating ? t('handover.creating') : t('handover.create')}
          </button>
        </div>
      </div>
    </div>
  );
}
