import { useEffect } from 'react';
import { MODES, PALETTES, type Appearance, type Theme } from '../appearance';
import { IconCheck, IconClose, IconPalette } from './icons';
import { t } from '../i18n';

interface Props {
  appearance: Appearance;
  /** The theme on screen now: what "follow the system" comes to at the moment. */
  theme: Theme;
  onChange: (next: Appearance) => void;
  onClose: () => void;
}

/**
 * The hub drawn very small in one palette and theme. It takes its colours from the same
 * variables as the real page: the attributes switch them for this one element.
 */
function Thumb({ palette, theme }: { palette: string; theme: Theme }) {
  return (
    <span className="look-thumb" data-palette={palette} data-theme={theme} aria-hidden="true">
      <span className="look-side">
        <span className="look-line" />
        <span className="look-line" />
        <span className="look-line" />
      </span>
      <span className="look-main">
        <span className="look-text" />
        <span className="look-text short" />
        <span className="look-box">
          <span className="look-dot" />
        </span>
      </span>
    </span>
  );
}

/** Light or dark, and which palette. Every choice shows at once on the page behind, and is kept on this device. */
export function AppearancePanel({ appearance, theme, onChange, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="dialog-scrim" onClick={onClose}>
      <div className="panel look-panel" role="dialog" aria-modal="true" aria-labelledby="look-title" onClick={(e) => e.stopPropagation()}>
        <header className="panel-head">
          <h2 id="look-title" className="panel-title">
            <IconPalette size={20} /> {t('appearance.title')}
          </h2>
          <button className="icon-btn" aria-label={t('common.close')} onClick={onClose}>
            <IconClose size={18} />
          </button>
        </header>
        <p className="panel-hint">{t('appearance.hint')}</p>

        <div className="panel-list look-body">
          <h3 id="look-mode" className="look-heading">
            {t('appearance.mode')}
          </h3>
          <div className="look-grid modes" role="radiogroup" aria-labelledby="look-mode">
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={appearance.mode === m.id}
                className="look-card"
                onClick={() => onChange({ ...appearance, mode: m.id })}
              >
                {m.id === 'system' ? (
                  <span className="look-split">
                    <Thumb palette={appearance.palette} theme="light" />
                    <Thumb palette={appearance.palette} theme="dark" />
                  </span>
                ) : (
                  <Thumb palette={appearance.palette} theme={m.id} />
                )}
                <span className="look-name">
                  {m.name}
                  {appearance.mode === m.id && <IconCheck size={14} />}
                </span>
              </button>
            ))}
          </div>

          <h3 id="look-palette" className="look-heading">
            {t('appearance.palette')}
          </h3>
          <div className="look-grid" role="radiogroup" aria-labelledby="look-palette">
            {PALETTES.map((p) => (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={appearance.palette === p.id}
                className="look-card"
                onClick={() => onChange({ ...appearance, palette: p.id })}
              >
                <Thumb palette={p.id} theme={theme} />
                <span className="look-name">
                  {p.name}
                  {appearance.palette === p.id && <IconCheck size={14} />}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
