import { useId, useState } from 'react';
import { t } from '../i18n';
import './DesktopPassword.css';

interface Props {
  wrong: boolean;
  onConnect: (password: string) => void;
  onCancel: () => void;
}

/** Authentication belongs to this viewer; the conversation keeps its focus and remains usable. */
export function DesktopPassword({ wrong, onConnect, onCancel }: Props) {
  const [password, setPassword] = useState('');
  const title = useId();
  const hint = useId();

  return (
    <div className="term-note desktop-auth">
      <form className="desktop-password" aria-labelledby={title}
        onSubmit={(e) => { e.preventDefault(); if (password) onConnect(password); }}
        onKeyDown={(e) => {
          const composing = e.nativeEvent.isComposing || e.keyCode === 229;
          if (e.key === 'Enter' && composing) e.preventDefault();
          if (e.key === 'Escape' && !composing) {
            e.stopPropagation();
            onCancel();
          }
        }}>
        <h2 id={title} className="dialog-title">{t('desktop.pwTitle')}</h2>
        <p id={hint} className="dialog-message" role={wrong ? 'alert' : undefined}>
          {wrong ? t('desktop.pwWrong') : t('desktop.pwMsg')}
        </p>
        <label className="dialog-field">
          <span className="sr-only">{t('desktop.pw')}</span>
          <input type="password" value={password} autoComplete="current-password" aria-describedby={hint}
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        <div className="dialog-actions">
          <button type="button" className="ghost" onClick={onCancel}>{t('common.cancel')}</button>
          <button type="submit" className="primary" disabled={!password}>{t('desktop.connect')}</button>
        </div>
      </form>
    </div>
  );
}
