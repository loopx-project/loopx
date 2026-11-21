import { useEffect, useState, useMemo } from 'react';
import type { AgentInfo, Api, StreamBridgeCapability, SupportedAgent } from '../api';
import { DialogSurface } from './Dialog';
import { AgentMark, IconCheck, IconClose } from './icons';
import { t } from '../i18n';
import { StreamBridgeOption } from './StreamBridgeOption';

export interface OnboardingDialogProps {
  api: Api;
  agents: AgentInfo[];
  initialStep?: 'ask' | 'results';
  onConnected: (newAgents: AgentInfo[]) => void;
  onClose: () => void;
  onLater: () => void;
}

interface InstalledItem {
  key: string;
  type: 'cli' | 'account';
  id: string;
  adapter: string;
  label: string;
  hint: string;
  icon?: string;
  command?: string;
  email?: string;
  env?: Record<string, string>;
  streamBridge?: StreamBridgeCapability;
}

interface UninstalledItem {
  adapter: string;
  label: string;
  icon?: string;
  installGuide?: string;
}

export function OnboardingDialog({
  api,
  agents,
  initialStep = 'ask',
  onConnected,
  onClose,
  onLater,
}: OnboardingDialogProps) {
  const [step, setStep] = useState<'ask' | 'results'>(initialStep);
  const [loading, setLoading] = useState(initialStep === 'results');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [discoveredClis, setDiscoveredClis] = useState<
    Array<{ id: string; adapter: string; command: string; label?: string; icon?: string }>
  >([]);
  const [discoveredAccounts, setDiscoveredAccounts] = useState<
    Array<{ adapter: string; email: string; env: Record<string, string>; id: string; label: string }>
  >([]);
  const [supportedList, setSupportedList] = useState<SupportedAgent[]>([]);
  const [streamBridges, setStreamBridges] = useState<Record<string, boolean>>({});

  const [connectedKeys, setConnectedKeys] = useState<Set<string>>(new Set());
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());

  // Load discovery data whenever entering the 'results' step
  useEffect(() => {
    if (step !== 'results') return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      api.discoverAgents().catch(() => []),
      api.discoverAccounts().catch(() => []),
      api.supportedAgents().catch(() => []),
    ]).then(([clis, accounts, supported]) => {
      if (cancelled) return;
      setDiscoveredClis(clis);
      setDiscoveredAccounts(accounts);
      setSupportedList(supported);

      // Select all discovered items by default
      const defaultSelected = new Set<string>();
      for (const cli of clis) {
        defaultSelected.add(`cli:${cli.id}`);
      }
      for (const acc of accounts) {
        defaultSelected.add(`acc:${acc.id}`);
      }
      setSelectedKeys(defaultSelected);
      setStreamBridges({});
      setConnectedKeys(new Set());
      setLoading(false);
    }).catch((e) => {
      if (cancelled) return;
      setError(e instanceof Error ? e.message : String(e));
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [api, step]);

  const supportedMap = useMemo(() => {
    return new Map(supportedList.map((s) => [s.adapter, s]));
  }, [supportedList]);

  // Items ready to be connected
  const installedItems = useMemo<InstalledItem[]>(() => {
    const items: InstalledItem[] = [];
    for (const cli of discoveredClis) {
      const sup = supportedMap.get(cli.adapter);
      const displayLabel = cli.id === cli.adapter ? (sup?.label ?? cli.id) : cli.id;
      items.push({
        key: `cli:${cli.id}`,
        type: 'cli',
        id: cli.id,
        adapter: cli.adapter,
        label: displayLabel,
        hint: cli.command,
        icon: cli.icon ?? sup?.icon,
        command: cli.command,
        streamBridge: sup?.capabilities?.streamBridge,
      });
    }
    for (const acc of discoveredAccounts) {
      const sup = supportedMap.get(acc.adapter);
      items.push({
        key: `acc:${acc.id}`,
        type: 'account',
        id: acc.id,
        adapter: acc.adapter,
        label: acc.label,
        hint: acc.email,
        icon: sup?.icon,
        email: acc.email,
        env: acc.env,
        streamBridge: sup?.capabilities?.streamBridge,
      });
    }
    return items;
  }, [discoveredClis, discoveredAccounts, supportedMap]);

  // Supported agents that are not yet installed on this host
  const uninstalledItems = useMemo<UninstalledItem[]>(() => {
    return supportedList.filter((s) => {
      const hasCli = discoveredClis.some((c) => c.adapter === s.adapter);
      const hasAcc = discoveredAccounts.some((a) => a.adapter === s.adapter);
      const hasAvailableInstance = agents.some((a) => a.adapter === s.adapter && a.available);
      return !hasCli && !hasAcc && !hasAvailableInstance;
    });
  }, [supportedList, discoveredClis, discoveredAccounts, agents]);

  const toggleSelect = (key: string, checked: boolean) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (checked) next.add(key);
      else next.delete(key);
      return next;
    });
  };

  const selectedCount = selectedKeys.size;

  const handleConnect = async () => {
    if (selectedCount === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const toAdd = installedItems.filter((item) => selectedKeys.has(item.key) && !connectedKeys.has(item.key));
      for (const item of toAdd) {
        if (item.type === 'cli') {
          await api.addAgent({
            id: item.id,
            adapter: item.adapter,
            label: item.label,
            command: [item.command!],
            ...(item.streamBridge ? { streamBridge: streamBridges[item.key] ?? false } : {}),
          });
        } else {
          await api.addAgent({
            id: item.id,
            adapter: item.adapter,
            label: item.label,
            env: item.env,
            account: item.email,
            ...(item.streamBridge ? { streamBridge: streamBridges[item.key] ?? false } : {}),
          });
        }
        setConnectedKeys(previous => new Set([...previous, item.key]));
      }
      const freshList = await api.agents();
      onConnected(freshList);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  if (step === 'ask') {
    return (
      <DialogSurface labelledBy="onboard-title" onClose={onLater} className="onboard-dialog">
        <div className="dialog-title-row">
          <h2 id="onboard-title" className="dialog-title">
            {t('onboard.askTitle')}
          </h2>
          <button type="button" className="icon-btn" aria-label={t('common.close')} onClick={onLater}>
            <IconClose size={18} />
          </button>
        </div>
        <p className="dialog-message">{t('onboard.askMessage')}</p>
        <div className="dialog-actions">
          <button type="button" className="ghost" onClick={onLater}>
            {t('onboard.later')}
          </button>
          <button type="button" className="primary" onClick={() => setStep('results')} autoFocus>
            {t('onboard.discover')}
          </button>
        </div>
      </DialogSurface>
    );
  }

  return (
    <DialogSurface labelledBy="onboard-title" onClose={onClose} busy={busy} className="onboard-dialog onboard-dialog-results">
      <div className="dialog-title-row">
        <h2 id="onboard-title" className="dialog-title">
          {t('onboard.resultTitle')}
        </h2>
        <button type="button" className="icon-btn" aria-label={t('common.close')} onClick={onClose} disabled={busy}>
          <IconClose size={18} />
        </button>
      </div>
      <p className="dialog-message">{t('onboard.resultMessage')}</p>

      {error && (
        <p className="field-hint bad" role="alert">
          {error}
        </p>
      )}

      {loading ? (
        <div className="onboard-loading">
          <span className="connect-spinner" aria-hidden="true" />
          <span>{t('onboard.scanning')}</span>
        </div>
      ) : (
        <div className="onboard-scroll-area">
          {/* Section: Installed on this host */}
          <div className="onboard-group">
            <div className="onboard-group-header">
              <span className="onboard-group-title">{t('onboard.installedGroup')}</span>
              <span className="onboard-group-count">{installedItems.length}</span>
            </div>
            {installedItems.length === 0 ? (
              <p className="onboard-empty">{t('onboard.noneInstalled')}</p>
            ) : (
              <div className="onboard-list" role="group" aria-label={t('onboard.installedGroup')}>
                {installedItems.map((item) => {
                  const isChecked = selectedKeys.has(item.key);
                  const connected = connectedKeys.has(item.key);
                  return (
                    <div key={item.key} className="onboard-entry">
                      <button
                        type="button"
                        role="checkbox"
                        aria-checked={isChecked}
                        disabled={busy || connected}
                        className={`onboard-row onboard-row-installed${isChecked ? ' checked' : ''}`}
                        onClick={() => toggleSelect(item.key, !isChecked)}
                      >
                        <span className={`onboard-checkbox${isChecked ? ' checked' : ''}`} aria-hidden="true">
                          {isChecked && <IconCheck size={12} />}
                        </span>
                        <span className="onboard-item-icon">
                          {item.icon ? <AgentMark path={item.icon} size={18} /> : null}
                        </span>
                        <span className="onboard-item-body">
                          <span className="onboard-item-name">{item.label}</span>
                          <span className="onboard-item-hint">{item.hint}</span>
                        </span>
                        {connected && <span className="onboard-badge">{t('onboard.connected')}</span>}
                      </button>
                      {item.streamBridge && <div className="onboard-entry-options">
                        <StreamBridgeOption capability={item.streamBridge} value={streamBridges[item.key] ?? false} disabled={busy || connected || !isChecked} compact
                          onChange={enabled => setStreamBridges(previous => ({ ...previous, [item.key]: enabled }))} />
                      </div>}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Section: Supported but not installed */}
          {uninstalledItems.length > 0 && (
            <div className="onboard-group onboard-group-uninstalled">
              <div className="onboard-group-header">
                <span className="onboard-group-title">{t('onboard.uninstalledGroup')}</span>
                <span className="onboard-group-count">{uninstalledItems.length}</span>
              </div>
              <div className="onboard-list" role="group" aria-label={t('onboard.uninstalledGroup')}>
                {uninstalledItems.map((item) => (
                  <div key={item.adapter} className="onboard-row onboard-row-uninstalled" aria-disabled="true">
                    <span className="onboard-item-icon">
                      {item.icon ? <AgentMark path={item.icon} size={18} /> : null}
                    </span>
                    <span className="onboard-item-body">
                      <span className="onboard-item-name">{item.label}</span>
                      {item.installGuide ? (
                        <span className="onboard-item-hint" title={item.installGuide}>
                          {item.installGuide}
                        </span>
                      ) : null}
                    </span>
                    <span className="onboard-badge">{t('onboard.notInstalled')}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="dialog-actions">
        <button type="button" className="ghost" onClick={onClose} disabled={busy}>
          {t('common.cancel')}
        </button>
        <button
          type="button"
          className="primary"
          disabled={busy || loading || selectedCount === 0}
          onClick={() => void handleConnect()}
        >
          {busy ? (
            <>
              <span className="connect-spinner" aria-hidden="true" />
              <span>{t('onboard.connecting')}</span>
            </>
          ) : (
            t('onboard.connectN', selectedCount)
          )}
        </button>
      </div>
    </DialogSurface>
  );
}
