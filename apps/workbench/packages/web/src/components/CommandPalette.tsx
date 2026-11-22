import { useEffect, useMemo, useRef, useState } from 'react';
import { t } from '../i18n';
import { PALETTE_GROUPS, filterPalette, type PaletteItem } from '../command-palette';
import { DialogSurface } from './Dialog';
import { IconSearch } from './icons';

/** ⌘K: jump to a session, a Goal, a settings page, or start something new. The caller supplies the rows and what opening each does. */
export function CommandPalette({ items, onPick, onClose }: {
  items: PaletteItem[];
  onPick: (item: PaletteItem) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const shown = useMemo(() => filterPalette(items, query), [items, query]);
  // Rows keep their group order so the keyboard walks the list the way the eye does.
  const ordered = useMemo(() => query.trim() ? shown : PALETTE_GROUPS.flatMap(group => shown.filter(item => item.group === group)), [shown, query]);
  // The dialog surface takes focus after its children mount; ask for it again once it has.
  useEffect(() => { const id = requestAnimationFrame(() => field.current?.focus()); return () => cancelAnimationFrame(id); }, []);
  useEffect(() => setActive(0), [query]);
  useEffect(() => { list.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }); }, [active]);
  const move = (by: number) => setActive(i => ordered.length ? (i + by + ordered.length) % ordered.length : 0);
  let lastGroup = '';
  return <DialogSurface labelledBy="palette-title" className="palette" onClose={onClose}>
    <h2 id="palette-title" className="sr-only">{t('palette.title')}</h2>
    <label className="palette-input">
      <IconSearch size={16} />
      <input
        ref={field} autoFocus role="combobox" aria-expanded="true" aria-controls="palette-list" aria-activedescendant={ordered[active] ? `palette-${active}` : undefined}
        value={query} placeholder={t('palette.placeholder')} onChange={e => setQuery(e.target.value)}
        onKeyDown={e => {
          if (e.nativeEvent.isComposing || e.keyCode === 229) return;
          if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
          else if (e.key === 'Enter' && ordered[active]) { e.preventDefault(); onPick(ordered[active]); }
        }}
      />
    </label>
    <div className="palette-list" id="palette-list" role="listbox" ref={list}>
      {ordered.length === 0 && <p className="palette-empty">{t('palette.empty')}</p>}
      {ordered.map((item, i) => {
        const head = !query.trim() && item.group !== lastGroup;
        lastGroup = item.group;
        return <div key={item.id}>
          {head && <div className="palette-group">{t(`palette.group.${item.group}`)}</div>}
          <button
            type="button" id={`palette-${i}`} role="option" aria-selected={i === active}
            className={`palette-row${i === active ? ' active' : ''}`}
            onMouseMove={() => i !== active && setActive(i)} onClick={() => onPick(item)}
          >
            <span className="palette-label">{item.label}</span>
            {item.needsYou && <span className="palette-badge">{t('palette.goalNeedsYou')}</span>}
            {item.hint && <span className="palette-hint">{item.hint}</span>}
          </button>
        </div>;
      })}
    </div>
    <footer className="palette-foot">{t('palette.hint')}</footer>
  </DialogSurface>;
}
