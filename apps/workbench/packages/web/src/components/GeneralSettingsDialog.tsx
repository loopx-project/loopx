import { useEffect, useState } from 'react';
import type { Api, GeneralSettings } from '../api';
import { t } from '../i18n';
import { Popover } from './Popover';
import { IconCheck, IconChevron } from './icons';
import { useStoredFlag } from '../hooks';
import { QUICK_SWITCH_HIDDEN_KEY } from '../quick-switch';
import { SettingsSection, usePageDraft } from './PageNavigation';

export function GeneralSettingsDialog({ api, onSave, onClose }: {
  api: Api; onSave: (value: GeneralSettings) => void; onClose: () => void;
}) {
  const [value, setValue] = useState<GeneralSettings | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [retry, setRetry] = useState(0);
  const [handleHidden, setHandleHidden] = useStoredFlag(QUICK_SWITCH_HIDDEN_KEY);
  useEffect(() => {
    let live = true;
    setError('');
    void api.generalSettings().then(value => { if (live) setValue(value); }).catch(error => { if (live) setError(String(error)); });
    return () => { live = false; };
  }, [api, retry]);

  usePageDraft(false, saving);

  const save = (patch: Partial<GeneralSettings>) => {
    if (!value || saving) return;
    const before = value;
    const next = { ...value, ...patch };
    if (next.startupPage === value.startupPage && next.autoArchiveDays === value.autoArchiveDays) return;
    // One write at a time keeps the shown choice aligned with the daemon's saved value.
    setValue(next); setSaving(true); setError('');
    void api.saveGeneralSettings(next).then(saved => { setValue(saved); onSave(saved); })
      .catch(error => { setValue(before); setError(String(error)); }).finally(() => setSaving(false));
  };
  const archiveOptions: GeneralSettings['autoArchiveDays'][] = [null, 1, 3, 7, 15, 30];
  const archiveLabel = (days: GeneralSettings['autoArchiveDays']) => days === null ? t('general.autoArchiveNever') : t('general.autoArchiveDays', days);

  return <SettingsSection title={t('general.title')} className="general-settings">
        {!value && !error && <p role="status">{t('common.loading')}</p>}
        {value && <>
          <div className="personal-setting-line general-setting-line">
            <span>{t('general.startupPage')}</span>
            <Popover label={t('general.startupPage')} triggerLabel={t('general.startupPage')} placement="below" align="end"
              triggerClassName="context-btn general-select" panelClassName="general-startup-menu" disabled={saving}
              trigger={<>{t(value.startupPage === 'new-thread' ? 'general.newThread' : 'general.recentThread')}<IconChevron size={12} open /></>}>
              {close => (['new-thread', 'recent-thread'] as const).map(startupPage => <button key={startupPage} type="button" className="pop-item compact"
                role="menuitemradio" aria-checked={value.startupPage === startupPage} onClick={() => { close(); save({ startupPage }); }}>
                <span className="pop-item-label">{t(startupPage === 'new-thread' ? 'general.newThread' : 'general.recentThread')}</span>
                <span className="pop-item-check">{value.startupPage === startupPage && <IconCheck size={14} />}</span>
              </button>)}
            </Popover>
          </div>
          <div className="personal-setting-line general-setting-line">
            <span>{t('general.autoArchive')}</span>
            <Popover label={t('general.autoArchive')} triggerLabel={t('general.autoArchive')} placement="below" align="end"
              triggerClassName="context-btn general-select" panelClassName="general-startup-menu" disabled={saving}
              trigger={<>{archiveLabel(value.autoArchiveDays)}<IconChevron size={12} open /></>}>
              {close => archiveOptions.map(autoArchiveDays => <button key={autoArchiveDays ?? 'never'} type="button" className="pop-item compact"
                role="menuitemradio" aria-checked={value.autoArchiveDays === autoArchiveDays} onClick={() => { close(); save({ autoArchiveDays }); }}>
                <span className="pop-item-label">{archiveLabel(autoArchiveDays)}</span>
                <span className="pop-item-check">{value.autoArchiveDays === autoArchiveDays && <IconCheck size={14} />}</span>
              </button>)}
            </Popover>
          </div>
          <p className="panel-hint general-hint">{t('general.startupHint')}</p>
          <div className="personal-setting-line general-setting-line">
            <span>{t('quickSwitch.handle')}</span>
            <Popover label={t('quickSwitch.handle')} triggerLabel={t('quickSwitch.handle')} placement="below" align="end"
              triggerClassName="context-btn general-select" panelClassName="general-startup-menu"
              trigger={<>{t(handleHidden ? 'quickSwitch.hidden' : 'quickSwitch.show')}<IconChevron size={12} open /></>}>
              {close => [false, true].map(hide => <button key={String(hide)} type="button" className="pop-item compact"
                role="menuitemradio" aria-checked={handleHidden === hide} onClick={() => { close(); setHandleHidden(hide); }}>
                <span className="pop-item-label">{t(hide ? 'quickSwitch.hidden' : 'quickSwitch.show')}</span>
                <span className="pop-item-check">{handleHidden === hide && <IconCheck size={14} />}</span>
              </button>)}
            </Popover>
          </div>
          <p className="panel-hint general-hint">{t('quickSwitch.handleHint')}</p>
          {saving && <p className="sr-only" role="status">{t('general.saving')}</p>}
        </>}
        {error && <p className="panel-error" role="alert">{error} {!value && <button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button>}</p>}
  </SettingsSection>;
}
