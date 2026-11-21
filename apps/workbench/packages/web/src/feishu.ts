import type { FeishuRealtime, FeishuStatus } from './api';
import { t } from './i18n';

const WARN_REALTIME: readonly FeishuRealtime[] = ['console-off', 'busy-elsewhere', 'unavailable', 'error'];

/**
 * The line beside the master switch: one short reading of how the channel stands, neutral while
 * messages can arrive and a warning the moment a person can fix something. Null while nothing is
 * known yet, so the page can hold its loading placeholder instead of reading as zero or off.
 */
export function feishuLine(status: FeishuStatus | null): { text: string; warn: boolean } | null {
  if (!status) return null;
  // The daemon's own reason for not being ready leads; the flags cover an older daemon.
  if (!status.cli || status.reason === 'cli_missing') return { text: t('feishu.cliMissing'), warn: true };
  if (!status.botReady || status.reason === 'bot_not_ready') return { text: t('feishu.botMissing'), warn: true };
  const realtime = status.realtime;
  if (realtime === 'live' || realtime === 'connecting' || realtime === 'off') {
    return { text: t(`feishu.realtime.${realtime}`), warn: false };
  }
  if (WARN_REALTIME.includes(realtime)) return { text: t(`feishu.realtime.${realtime}`), warn: true };
  return null;
}

/** A daemon reason code in words; an unknown code passes through, no reason stays empty. */
export function feishuReason(value: string | null): string | null {
  if (!value) return null;
  switch (value) {
    case 'cli_missing': return t('feishu.reason.cliMissing');
    case 'bot_not_ready': return t('feishu.reason.botNotReady');
    case 'timeout': return t('feishu.reason.timeout');
    case 'owner_missing': return t('feishu.reason.ownerMissing');
    case 'cli_failed': return t('feishu.reason.cliFailed');
    case 'not_owner': return t('feishu.reason.notOwner');
    case 'rate_limit': return t('feishu.reason.rateLimit');
    case 'console_off': return t('feishu.reason.consoleOff');
    case 'unsupported': return t('feishu.reason.unsupported');
    default: return value;
  }
}

/** A record's outcome in words, next to the word for its direction. */
export function feishuResult(result: 'received' | 'sent' | 'failed' | 'dropped'): string {
  return t(`feishu.result.${result}`);
}

export type FeishuSetupKind = 'create' | 'login';

/** The words a pairing panel uses, by what it is pairing: a new bot, or the owner's identity. */
export function feishuSetupCopy(kind: FeishuSetupKind | null | undefined): { hint: string; waiting: string; failed: string } {
  return kind === 'login'
    ? { hint: t('feishu.loginHint'), waiting: t('feishu.loginWaitingHint'), failed: t('feishu.loginFailed') }
    : { hint: t('feishu.setupHint'), waiting: t('feishu.waitingHint'), failed: t('feishu.setupFailed') };
}

/** Whether a sign-in panel belongs on the configured page: only while the login is out or failed. */
export function feishuLoginPending(status: FeishuStatus | null): boolean {
  return !!status && status.setup.kind === 'login' && (status.setup.state === 'waiting' || status.setup.state === 'failed');
}
