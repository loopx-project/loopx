import { useId } from 'react';
import type { StreamBridgeCapability } from '../api';
import { t } from '../i18n';

/** Consent is separate from selecting an instance; no brand or environment-variable knowledge. */
export function StreamBridgeOption({ capability, value, onChange, disabled = false, compact = false }: {
  capability: StreamBridgeCapability;
  value: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const hintId = useId();
  return <div className={`agent-bridge-option${compact ? ' compact' : ''}`}>
    <div className="agent-bridge-line">
      <span>{t('streamBridge.enable')}</span>
      <button type="button" className="personal-switch" role="switch" aria-label={t('streamBridge.enable')} aria-describedby={hintId}
        aria-checked={value} disabled={disabled} onClick={() => onChange(!value)}><span /></button>
    </div>
    <p id={hintId} className="agent-bridge-hint">{t('streamBridge.hint', capability.platform)}</p>
    {!compact && <p className="agent-bridge-hint">{t('streamBridge.footprint', capability.temporaryMegabytes)}</p>}
  </div>;
}
