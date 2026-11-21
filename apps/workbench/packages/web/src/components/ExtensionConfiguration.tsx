import { useEffect, useState } from 'react';
import type { ExtensionConfiguration as Configuration, ExtensionState, WorkBinding } from '@calli/extension-api';
import type { Api, Thread } from '../api';
import { Dialog } from './Dialog';
import { Popover } from './Popover';
import { IconCheck, IconChevron } from './icons';
import { locale, t } from '../i18n';

export function ExtensionConfiguration({ api, extension, extensions, threads, onClose, onSaved, onError }: {
  api: Api; extension: ExtensionState; extensions: ExtensionState[]; threads: Thread[]; onClose(): void; onSaved(): void; onError(e: unknown): void;
}) {
  const [config, setConfig] = useState<Configuration | null>(null);
  const [busy, setBusy] = useState(false);
  const [detection, setDetection] = useState<string | null>(null);
  useEffect(() => { let alive = true; void api.extensionConfiguration(extension.manifest.id).then(c => { if (alive) setConfig(c); }).catch(onError); return () => { alive = false; }; }, [api, extension.manifest.id]);
  const fields = extension.manifest.settings ?? [];
  const update = (id: string, patch: Partial<WorkBinding>) => setConfig(c => c && ({ ...c, bindings: c.bindings.map(b => b.id === id ? { ...b, ...patch } : b) }));
  const save = async () => {
    if (!config) return;
    setBusy(true);
    try { await api.configureExtension(extension.manifest.id, config); onSaved(); onClose(); }
    catch (e) { onError(e); } finally { setBusy(false); }
  };
  return <Dialog title={t('work.configure', extension.manifest.name[locale()])} confirmLabel={t('work.save')} confirmDisabled={!config || busy} onCancel={() => !busy && onClose()} onConfirm={() => void save()} className="ext-config-dialog">
    {!config ? <p>{t('common.loading')}</p> : <div className="ext-config-body">
      {fields.filter(f => f.scope === 'installation').map(f => <label className="ext-field" key={f.key}><span>{f.label[locale()]}</span><input value={config.settings[f.key] ?? f.default ?? ''} onChange={e => setConfig({ ...config, settings: { ...config.settings, [f.key]: e.target.value } })} /></label>)}
      <div className="ext-detect"><button type="button" className="ghost" disabled={busy || !extension.enabled} onClick={async () => {
        setBusy(true); try { await api.configureExtension(extension.manifest.id, config); const d = await api.detectExtension(extension.manifest.id); setDetection(`${d.ok ? t('work.available') : t('work.unverified')} ${d.version ?? ''} ${d.missing ?? ''}`); onSaved(); } catch (e) { onError(e); } finally { setBusy(false); }
      }}>{t('work.detect')}</button>{detection && <span role="status">{detection}</span>}</div>
      <p className="ext-config-hint">{t('work.bindHint')}</p>
      {config.bindings.map((b, index) => <section className="ext-binding" key={b.id}>
        <div className="ext-binding-head"><strong>{t('work.binding', index + 1)}</strong><button className="ghost" type="button" onClick={() => setConfig({ ...config, bindings: config.bindings.filter(v => v.id !== b.id) })}>{t('work.removeBinding')}</button></div>
        <label className="ext-field"><span>{t('work.externalId')}</span><input value={b.externalId} onChange={e => update(b.id, { externalId: e.target.value })} /></label>
        {fields.filter(f => f.scope === 'binding').map(f => <label className="ext-field" key={f.key}><span>{f.label[locale()]}</span><input value={b.settings[f.key] ?? f.default ?? ''} onChange={e => update(b.id, { settings: { ...b.settings, [f.key]: e.target.value } })} /></label>)}
        <Popover label={t('work.threads')} triggerClassName="ghost ext-thread-picker" trigger={<>{t('work.threadCount', b.threadIds.length)}<IconChevron size={12} open /></>}>
          {() => <>{threads.filter(th => !th.archivedAt).map(th => <button type="button" className="pop-item" key={th.id} onClick={() => update(b.id, { threadIds: b.threadIds.includes(th.id) ? b.threadIds.filter(id => id !== th.id) : [...b.threadIds, th.id] })}><span className="pop-item-text">{th.title || th.id}</span>{b.threadIds.includes(th.id) && <IconCheck size={14} />}</button>)}</>}
        </Popover>
        <label className="ext-field"><span>{t('work.sourceUrl')}</span><input value={b.url ?? ''} onChange={e => update(b.id, { url: e.target.value })} /></label>
        {extensions.filter(s => s.source === 'builtin' && s.manifest.permissions.includes('worksource.read')).map(s => <label className="dialog-option" key={s.manifest.id}><input type="checkbox" checked={b.consumers.includes(s.manifest.id)} onChange={e => update(b.id, { consumers: e.target.checked ? [...b.consumers, s.manifest.id] : b.consumers.filter(id => id !== s.manifest.id) })} /><span>{t('work.shareWith', s.manifest.name[locale()])}</span></label>)}
      </section>)}
      <button type="button" className="ghost" onClick={() => setConfig({ ...config, bindings: [...config.bindings, { id: 'b' + crypto.randomUUID(), externalId: '', settings: {}, threadIds: [], consumers: [] }] })}>{t('work.addBinding')}</button>
    </div>}
  </Dialog>;
}
