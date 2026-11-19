import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { useIsCompact } from '../hooks';
import { t } from '../i18n';
import { IconBack, IconChevron } from './icons';

export type SettingsEntry = {
  id: string; label: string; icon: (props: { size?: number }) => JSX.Element;
  render?: () => ReactNode; onOpen?: () => void;
};
export type SettingsGroup = { label: string; entries: SettingsEntry[] };

export function SettingsPage({ groups, path, onNavigate, onClose, detailTitle, closeOnRoot = false }: {
  groups: SettingsGroup[]; path: string; onNavigate: (path: string) => void; onClose: () => void; detailTitle?: string; closeOnRoot?: boolean;
}) {
  const compact = useIsCompact();
  const entries = groups.flatMap(group => group.entries);
  const selected = entries.find(entry => entry.id === path.split('/')[0]) ?? (!compact ? entries.find(entry => entry.render) : undefined);
  const showingDetail = Boolean(selected?.render && (!compact || path));
  const heading = useRef<HTMLHeadingElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const positions = useRef(new Map<string, number>());
  const title = showingDetail ? detailTitle ?? selected!.label : t('sidebar.settings');
  useLayoutEffect(() => {
    heading.current?.focus({ preventScroll: true });
    const el = scroller.current;
    if (el) el.scrollTop = positions.current.get(path) ?? 0;
    return () => { if (el) positions.current.set(path, el.scrollTop); };
  }, [path]);
  const back = () => {
    if (path.includes('/')) onNavigate(path.slice(0, path.lastIndexOf('/')));
    else if (compact && showingDetail && !closeOnRoot) onNavigate('');
    else onClose();
  };
  return <div className={`settings-page${showingDetail ? ' has-detail' : ''}`}>
    <header className="settings-topbar">
      <button className="icon-btn glass-control" aria-label={t('common.back')} onClick={back}><IconBack size={21} /></button>
      <h1 ref={heading} tabIndex={-1}>{compact ? title : t('sidebar.settings')}</h1>
      <span className="settings-header-spacer" />
    </header>
    <div className="settings-layout">
      <nav className="settings-nav" aria-label={t('settings.categories')}>
        {groups.map(group => <div className="settings-nav-group" key={group.label}>
          <h2>{group.label}</h2>
          <div className="settings-nav-list">{group.entries.map(entry => <button key={entry.id} type="button"
            className={`settings-nav-row${selected?.id === entry.id ? ' selected' : ''}`}
            aria-current={selected?.id === entry.id ? 'page' : undefined}
            onClick={() => entry.onOpen ? entry.onOpen() : onNavigate(entry.id)}>
            <entry.icon size={18} /><span>{entry.label}</span><IconChevron size={14} />
          </button>)}</div>
        </div>)}
      </nav>
      {showingDetail && <div className="settings-detail" ref={scroller} key={selected!.id}>
        {selected!.render!()}
      </div>}
    </div>
  </div>;
}
