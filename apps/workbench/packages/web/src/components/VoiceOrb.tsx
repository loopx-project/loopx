import { useEffect, useId, useRef, useState } from 'react';
import type { VoiceSessionState } from '../voice-session';
import { Popover } from './Popover';
import { IconClose, IconMic, IconMicOff } from './icons';
import { t } from '../i18n';

export function VoiceCloud() {
  const id = useId().replace(/:/g, '');
  return <svg viewBox="0 0 200 200" aria-hidden="true">
    <defs>
      <filter id={`${id}-cloud`} x="0" y="0" width="100%" height="100%">
        <feTurbulence type="fractalNoise" baseFrequency="0.011 0.034" numOctaves="4" seed="7" stitchTiles="stitch" />
        <feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 2.6 -1.05" />
        <feComposite in="SourceGraphic" operator="in" />
      </filter>
      <linearGradient id={`${id}-sky`} x2="0" y2="1">
        <stop offset="0" stopColor="var(--voice-top)" /><stop offset=".5" stopColor="var(--voice-mid)" /><stop offset="1" stopColor="var(--voice-low)" />
      </linearGradient>
      <linearGradient id={`${id}-band`} x2="0" y2="1">
        <stop offset=".28" stopColor="var(--voice-cloud)" stopOpacity="0" /><stop offset=".52" stopColor="var(--voice-cloud)" /><stop offset=".72" stopColor="var(--voice-cloud)" stopOpacity=".35" /><stop offset=".9" stopColor="var(--voice-cloud)" stopOpacity="0" />
      </linearGradient>
      <mask id={`${id}-mask`}><rect width="200" height="200" fill={`url(#${id}-band)`} /></mask>
      <clipPath id={`${id}-disc`}><circle cx="100" cy="100" r="100" /></clipPath>
    </defs>
    <g clipPath={`url(#${id}-disc)`}>
      <rect width="200" height="200" fill={`url(#${id}-sky)`} />
      <g mask={`url(#${id}-mask)`} fill="var(--voice-cloud)">
        <g className="voice-cloud"><rect width="600" height="200" filter={`url(#${id}-cloud)`} /></g>
        <g className="voice-cloud secondary"><rect width="600" height="200" y="14" filter={`url(#${id}-cloud)`} transform="scale(1 .8)" /></g>
      </g>
    </g>
  </svg>;
}

export function VoiceOrb({ session, provider, empty, levels, tasks = [], awaitingApproval, onTask }: {
  session: VoiceSessionState; provider: string; empty: boolean;
  levels: () => { input: number; output: number };
  tasks?: { id: string; title: string; since?: string | null }[];
  awaitingApproval: boolean; onTask: (id?: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [now, setNow] = useState(Date.now());
  const phase = session.error ? 'error' : session.status !== 'connected' ? 'connecting' : session.isMuted ? 'muted' : session.phase;
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let frame = 0, speakingUntil = 0;
    const tick = () => {
      const audio = levels();
      if (audio.output > .015) speakingUntil = performance.now() + 300;
      const current = phase === 'listening' || phase === 'thinking' || phase === 'speaking'
        ? performance.now() < speakingUntil ? 'speaking' : phase : phase;
      if (ref.current) {
        ref.current.dataset.phase = current;
        ref.current.style.setProperty('--voice-level', String(current === 'speaking' ? audio.output : audio.input));
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [phase, levels]);
  const seconds = Math.max(0, Math.floor((now - (session.startedAt ?? now)) / 1000));
  const detail = `${t('voice.via', provider)} · ${t('voice.duration', Math.floor(seconds / 60), seconds % 60)}`;
  const status = session.error || (phase === 'connecting' ? t('voice.connecting') : phase === 'muted' ? t('voice.muted') : '');
  return <div className={`voice-stage${empty && !awaitingApproval && !session.error ? ' voice-stage-empty' : ''}`}>
    {awaitingApproval ? <button className="voice-task awaiting" onClick={() => onTask()}>{t('voice.awaitingApproval')}</button>
      : tasks.length > 0 && <Popover label={t('voice.tasks', tasks.length)} triggerClassName="voice-task" trigger={<><span className="send-spinner" />{tasks.length === 1 ? tasks[0].title : t('voice.tasks', tasks.length)}{tasks.length === 1 && tasks[0].since && <span>{Math.max(0, Math.floor((now - Date.parse(tasks[0].since)) / 60000))}:{String(Math.max(0, Math.floor((now - Date.parse(tasks[0].since)) / 1000)) % 60).padStart(2, '0')}</span>}</>}>
        {close => <>{tasks.map(task => <button className="pop-item" role="menuitem" key={task.id} onClick={() => { close(); onTask(task.id); }}>{task.title}</button>)}</>}
      </Popover>}
    <div ref={ref} className="voice-orb" data-phase={phase}>
      <Popover label={t('voice.title')} triggerClassName="voice-orb-trigger" triggerTitle={detail} triggerLabel={t('voice.details')} trigger={<VoiceCloud />}>
        {() => <div className="voice-details"><strong>{t('voice.via', provider)}</strong><span>{t('voice.duration', Math.floor(seconds / 60), seconds % 60)}</span></div>}
      </Popover>
    </div>
    {status && <div className={`voice-status${session.error ? ' error' : ''}`} role={session.error ? 'alert' : 'status'}>{status}</div>}
  </div>;
}

export function VoiceControls({ session, onMute, onStop }: { session: VoiceSessionState; onMute: () => void; onStop: () => void }) {
  return <div className="voice-controls">
    {session.active && <button className={`voice-round${session.isMuted ? ' selected' : ''}`} aria-label={t(session.isMuted ? 'voice.unmute' : 'voice.mute')} title={t(session.isMuted ? 'voice.unmute' : 'voice.mute')} onClick={onMute}>{session.isMuted ? <IconMicOff size={20} /> : <IconMic size={20} />}</button>}
    <button className="voice-round end" aria-label={t(session.active ? 'voice.end' : 'voice.dismiss')} title={t(session.active ? 'voice.end' : 'voice.dismiss')} onClick={onStop}><IconClose size={20} /></button>
  </div>;
}
