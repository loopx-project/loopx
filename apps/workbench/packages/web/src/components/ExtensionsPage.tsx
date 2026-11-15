import { ExtensionConfiguration } from './ExtensionConfiguration';
import { useState } from 'react';
import type { ExtensionState, Permission, ExtensionPackagePreview } from '@calli/extension-api';
import type { Api, Thread } from '../api';
import { locale, t, type MessageKey } from '../i18n';
import { HostIcon } from '../extensions/ui';
import { needsConfirm } from '../extensions/permissions';
import { enabledViews, extensionHash } from '../extensions/routes';
export { needsConfirm } from '../extensions/permissions';
import { Dialog } from './Dialog';
import { IconPuzzle, IconShield } from './icons';

interface Props {
  api: Api;
  threads?: Thread[];
  onReload?: () => void;
  /** The made-up workspace: installs are kept on the page only. */
  demo?: boolean;
  /** The catalog and this machine's state, owned by App; null until read. */
  extensions: ExtensionState[] | null;
  /** One extension's new state, straight from the answer: the list shows it before the broadcast comes back. */
  onChange: (state: ExtensionState) => void;
  onOpenView: (hash: string) => void;
  onError: (e: unknown) => void;
}

const permissionLabel = (p: Permission) => t(`extensions.perm.${p}` as MessageKey);
const permissionHint = (p: Permission) => t(`extensions.perm.${p}.hint` as MessageKey);

function Permissions({ list }: { list: Permission[] }) {
  if (!list.length) return <p className="ext-perms-none">{t('extensions.noPermissions')}</p>;
  return (
    <ul className="ext-perms">
      {list.map((p) => (
        <li key={p}>
          <span className="ext-perm-icon" aria-hidden="true">
            <IconShield size={15} />
          </span>
          <span className="ext-perm-text">
            <span className="ext-perm-label">{permissionLabel(p)}</span>
            <span className="ext-perm-hint">{permissionHint(p)}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Settings → Extensions: the Calli extensions this hub ships, what each may read, and installing or
 * removing them on the machine on screen. Daily work happens in the views they add, not here.
 */
export function ExtensionsPage({ api, demo = false, extensions, threads = [], onReload, onChange, onOpenView, onError }: Props) {
  const [installing, setInstalling] = useState<ExtensionState | null>(null);
  const [removing, setRemoving] = useState<ExtensionState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const lang = locale();
  const [adding, setAdding] = useState(false);
  const [origin, setOrigin] = useState('');
  const [sha256, setSha256] = useState('');
  const [preview, setPreview] = useState<ExtensionPackagePreview | null>(null);
  const [configuring, setConfiguring] = useState<ExtensionState | null>(null);

  const run = async (id: string, work: () => Promise<ExtensionState | void>) => {
    setBusy(id);
    try {
      const next = await work();
      if (next) onChange(next);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="todos-page ext-page">
      <header className="todos-head">
        <div className="ext-page-heading"><h1 className="todos-title">{t('extensions.title')}</h1>{!demo && <button className="primary" onClick={() => setAdding(true)}>{t('work.addPackage')}</button>}</div>
        <p className="todos-hint">{t('extensions.hint')}</p>
        {demo && <p className="todos-hint ext-demo-note">{t('extensions.demoNote')}</p>}
      </header>

      {extensions === null ? (
        <p className="panel-empty">{t('common.loading')}</p>
      ) : extensions.length === 0 ? (
        <p className="panel-empty">{t('extensions.none')}</p>
      ) : (
        <div className="ext-cards">
          {extensions.map((s) => {
            const m = s.manifest;
            const icon = m.contributes.views?.[0]?.icon ?? m.contributes.composerActions?.[0]?.icon;
            const confirm = needsConfirm(s);
            const entry = !confirm ? enabledViews([s])[0] : undefined;
            const reqBlocked = s.requirements !== undefined && s.requirements.state !== 'satisfied';
            return (
              <article key={m.id} className={`ext-card${s.installed ? ' installed' : ''}`} aria-busy={busy === m.id}>
                <div className="ext-card-head">
                  <span className="ext-card-icon" aria-hidden="true">
                    {icon ? <HostIcon name={icon} size={20} /> : <IconPuzzle size={20} />}
                  </span>
                  <div className="ext-card-titles">
                    <h2 className="ext-card-name">{m.name[lang]}</h2>
                    <div className="ext-card-meta">
                      <span>{t('extensions.version', m.version)}</span>
                      <span>{t(s.preset ? 'work.supported' : s.source === 'external' ? 'work.external' : 'extensions.builtin')}</span>
                      {!s.installed && s.preset && <span>{t('work.notInstalled')}</span>}
                      {s.installed && <span className={`ext-card-state${s.enabled ? ' on' : ''}`}>{confirm ? t('extensions.needsConfirm') : s.enabled ? t('extensions.installed') : t('extensions.disabled')}</span>}
                      {reqBlocked && (
                        <span className="ext-req-tag warning" title={s.requirements?.reason ?? t('extensions.notSatisfied')}>
                          {t('extensions.requirementMissing', s.requirements?.reason ?? s.requirements?.state ?? '')}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="ext-card-actions">
                    {entry && (
                      <button type="button" className="primary" disabled={busy !== null} onClick={() => onOpenView(extensionHash(entry))}>
                        {t('extensions.open')}
                      </button>
                    )}
                    {!s.installed || confirm ? (
                      <button
                        type="button"
                        className="primary"
                        disabled={busy !== null || reqBlocked}
                        title={reqBlocked ? (s.requirements?.reason ?? t('extensions.notSatisfied')) : undefined}
                        onClick={() => {
                          if (s.source === 'external' && s.origin) void run(m.id, async () => { setPreview(await api.prepareExtension(s.origin!)); });
                          else setInstalling(s);
                        }}
                      >
                        {confirm ? t('extensions.confirmAgain') : t('extensions.install')}
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="ghost"
                        disabled={busy !== null || (!s.enabled && reqBlocked)}
                        title={!s.enabled && reqBlocked ? (s.requirements?.reason ?? t('extensions.cannotEnable')) : undefined}
                        onClick={() => void run(m.id, () => api.setExtensionEnabled(m.id, !s.enabled))}
                      >
                        {s.enabled ? t('extensions.disable') : t('extensions.enable')}
                      </button>
                    )}
                    {s.installed && (
                      <button type="button" className="ghost ext-remove" disabled={busy !== null} onClick={() => setRemoving(s)}>
                        {t('extensions.uninstall')}
                      </button>
                    )}
                  </div>
                </div>
                {s.source === 'external' && s.installed && <div className="ext-external-actions"><button className="ghost" onClick={() => setConfiguring(s)}>{t('work.settings')}</button>{s.previousVersion && <button className="ghost" disabled={busy !== null} onClick={() => void run(m.id, () => api.rollbackExtension(m.id))}>{t('work.rollback', s.previousVersion)}</button>}<span className="ext-origin" title={s.origin}>{s.origin}</span></div>}
                <p className="ext-card-desc">{m.description[lang]}</p>
                {s.preset && !s.installed && <p className="ext-config-hint">{t('work.toolPrerequisite')}</p>}
                <div className="ext-card-section">{t('extensions.permissions')}</div>
                <Permissions list={m.permissions} />
              </article>
            );
          })}
        </div>
      )}

      {adding && <Dialog title={t('work.addPackage')} confirmLabel={t('work.inspect')} confirmDisabled={!origin.trim() || busy !== null} onCancel={() => setAdding(false)} onConfirm={() => void run('prepare', async () => { const p = await api.prepareExtension(origin.trim(), sha256.trim() || undefined); setPreview(p); setAdding(false); })}>
        <p className="ext-config-hint">{t('work.packageHint')}</p>
        <label className="ext-field"><span>{t('work.packageSource')}</span><input value={origin} onChange={e => setOrigin(e.target.value)} /></label>
        {origin.startsWith('https://') && <label className="ext-field"><span>{t('work.checksum')}</span><input value={sha256} onChange={e => setSha256(e.target.value)} /></label>}
      </Dialog>}
      {preview && <Dialog title={t('extensions.installTitle', preview.manifest.name[lang])} message={t('work.trustPackage')} confirmLabel={t('extensions.install')} confirmDisabled={busy !== null} onCancel={() => setPreview(null)} onConfirm={() => void run(preview.manifest.id, async () => { const state = await api.commitExtension(preview.token, preview.manifest.permissions); setPreview(null); onReload?.(); return state; })}>
        <p className="ext-origin">{preview.origin} · {preview.manifest.version}</p><Permissions list={preview.manifest.permissions} />
        <div className="ext-command-list">{preview.manifest.commands?.map(c => <p key={c.id}><code>{c.id}</code><span className="ext-command-spec">{JSON.stringify([c.executable, ...c.args])}</span> · {t(c.effect === 'runtime' ? 'work.runtimeEffect' : 'work.readEffect')}</p>)}</div>
      </Dialog>}
      {configuring && <ExtensionConfiguration api={api} extension={configuring} extensions={extensions ?? []} threads={threads} onClose={() => setConfiguring(null)} onSaved={() => onReload?.()} onError={onError} />}
      {installing && (
        <Dialog
          title={t('extensions.installTitle', installing.manifest.name[lang])}
          message={t('extensions.installMessage')}
          confirmLabel={t('extensions.install')}
          className="ext-install-dialog"
          onCancel={() => setInstalling(null)}
          onConfirm={() => {
            const s = installing;
            setInstalling(null);
            void run(s.manifest.id, () => api.installExtension(s.manifest.id, s.manifest.permissions));
          }}
        >
          <Permissions list={installing.manifest.permissions} />
          {installing.manifest.permissions.includes('events.summary') && <p className="ext-install-note">{t('extensions.titleNote')}</p>}
        </Dialog>
      )}
      {removing && (
        <Dialog
          title={t('extensions.uninstallTitle', removing.manifest.name[lang])}
          message={t('extensions.uninstallMessage')}
          confirmLabel={t('extensions.uninstall')}
          danger
          onCancel={() => setRemoving(null)}
          onConfirm={() => {
            const s = removing;
            setRemoving(null);
            void run(s.manifest.id, async () => {
              await api.uninstallExtension(s.manifest.id);
              if (onReload && !demo) { onReload(); return; }
              return { ...s, installed: false, enabled: false, granted: [], installedAt: null, updatedAt: null };
            });
          }}
        />
      )}
    </div>
  );
}
