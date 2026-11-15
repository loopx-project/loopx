#!/usr/bin/env node
// Probe the native app-server, never a separate OpenAI API endpoint. No credentials are logged.
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';

/** Silent synthetic track: tests transport without requesting a person's microphone. */
async function webrtcProbe() {
  const { chromium } = await import('playwright-core');
  const candidates = [undefined, join(homedir(), '.cache/ms-playwright/chromium-1243/chrome-linux64/chrome'), '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable'];
  let browser;
  for (const executablePath of candidates) {
    if (executablePath && !existsSync(executablePath)) continue;
    try { browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] }); break; } catch { /* Try an installed browser. */ }
  }
  if (!browser) throw new Error('WebRTC probe browser unavailable');
  try {
    const page = await browser.newPage();
    const sdp = await page.evaluate(async () => {
      const pc = new RTCPeerConnection();
      const audio = new AudioContext();
      const destination = audio.createMediaStreamDestination();
      const oscillator = audio.createOscillator();
      const gain = audio.createGain(); gain.gain.value = 0;
      oscillator.connect(gain).connect(destination); oscillator.start();
      pc.addTrack(destination.stream.getAudioTracks()[0], destination.stream);
      pc.createDataChannel('oai-events');
      globalThis.voiceProbe = { pc, audio, oscillator };
      await audio.resume();
      await pc.setLocalDescription(await pc.createOffer());
      if (pc.iceGatheringState !== 'complete') await new Promise(resolve => {
        const timer = setTimeout(resolve, 2000);
        pc.addEventListener('icegatheringstatechange', () => { if (pc.iceGatheringState === 'complete') { clearTimeout(timer); resolve(); } });
      });
      return pc.localDescription.sdp;
    });
    return {
      sdp,
      answer: sdp => page.evaluate(sdp => globalThis.voiceProbe.pc.setRemoteDescription({ type: 'answer', sdp }), sdp),
      state: () => page.evaluate(() => globalThis.voiceProbe.pc.connectionState),
      close: () => browser.close(),
    };
  } catch (error) { await browser.close(); throw error; }
}

function diagnostic(error) {
  const text = String(error?.message ?? error);
  // Return classifications, not upstream messages: those may contain credentials or private URLs.
  if (/requires API key auth/i.test(text)) return 'api_key_auth_required';
  if (/text realtime output modality requires realtime v2/i.test(text)) return 'text_requires_v2';
  if (/requires.*webrtc|webrtc.*required/i.test(text)) return 'webrtc_required';
  if (/not supported|unsupported|method not found/i.test(text)) return 'unsupported';
  if (/401|unauthorized|authentication|not authenticated/i.test(text)) return 'authentication_failed';
  if (/403|forbidden|not entitled|access denied/i.test(text)) return 'access_denied';
  if (/timeout|timed out/i.test(text)) return 'timeout';
  if (/ENOENT/.test(text)) return 'command_not_found';
  if (/browser unavailable/i.test(text)) return 'browser_unavailable';
  return 'native_error';
}

/** Read-only unless connect is explicit. A connection probe uses an empty ephemeral thread. */
export async function probeVoice({
  command = ['codex', 'app-server'], connect = false, protocol, audioOutput = false, webrtc = false,
  requestTimeoutMs = 15_000, observeMs = 8_000, env = process.env,
} = {}) {
  if (protocol !== undefined && !['v1', 'v2', 'v3'].includes(protocol)) throw new Error('Invalid realtime protocol');
  const cwd = await mkdtemp(join(tmpdir(), 'calli-voice-probe-'));
  const report = {
    mode: connect ? 'connection' : 'capabilities', protocol: protocol ?? 'default',
    transport: connect ? (webrtc ? 'webrtc' : 'websocket') : null, outputModality: connect ? (audioOutput ? 'audio' : 'text') : null,
    accountType: null, interfaceAvailable: false, voiceCounts: null,
    startAccepted: false, startedNotification: false, nativeSessionIdObserved: false,
    audioVerified: false, subscriptionBillingVerified: false,
    peerConnectionState: null, sdpReceived: false,
    status: 'unverified', error: null, stage: 'initialize', notifications: [],
    stopAcknowledged: false, processClosed: false,
  };
  let threadId, nextId = 0, processEnded = false, processError, media;
  let answerTask = Promise.resolve();
  const pending = new Map();
  const child = spawn(command[0], command.slice(1), { cwd, env, stdio: ['pipe', 'pipe', 'pipe'] });
  let finishObservation;
  const observation = new Promise(resolve => { finishObservation = resolve; });
  const failPending = error => {
    for (const p of pending.values()) p.reject(error);
    pending.clear();
  };
  const closed = new Promise(resolve => {
    child.once('close', () => { processEnded = true; failPending(new Error('App-server closed')); finishObservation(); resolve(); });
  });
  child.on('error', error => { processError = error; failPending(error); });
  child.stdin.on('error', error => failPending(error));
  child.stderr.resume(); // Deliberately do not forward native logs, which can contain account data.
  const write = value => {
    if (processEnded || processError || child.stdin.destroyed) throw processError ?? new Error('App-server closed');
    child.stdin.write(JSON.stringify(value) + '\n');
  };
  const rpc = (method, params, timeout = requestTimeoutMs) => new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('RPC timeout')); }, timeout);
    const entry = {
      resolve: value => { clearTimeout(timer); pending.delete(id); resolve(value); },
      reject: error => { clearTimeout(timer); pending.delete(id); reject(error); },
    };
    pending.set(id, entry);
    try { write({ id, method, params }); } catch (error) { entry.reject(error); }
  });
  const lines = createInterface({ input: child.stdout });
  const knownNotifications = new Set([
    'thread/realtime/started', 'thread/realtime/closed', 'thread/realtime/error',
    'thread/realtime/sdp', 'thread/realtime/transcript/delta', 'thread/realtime/transcript/done',
    'thread/realtime/item/started', 'thread/realtime/item/completed',
    'thread/realtime/item/transcript/delta', 'thread/realtime/outputAudio/delta',
  ]);
  lines.on('line', line => {
    let message;
    try { message = JSON.parse(line); } catch { return; }
    if (message.id !== undefined && message.method === undefined) {
      const p = pending.get(message.id);
      if (message.error) p?.reject(new Error(String(message.error.message)));
      else p?.resolve(message.result);
      return;
    }
    if (message.id !== undefined) {
      try { write({ id: message.id, error: { code: -32601, message: 'Diagnostic does not execute tools or approve requests' } }); } catch { /* Process is closing. */ }
      return;
    }
    if (!threadId || message.params?.threadId !== threadId || !knownNotifications.has(message.method)) return;
    if (!report.notifications.includes(message.method)) report.notifications.push(message.method);
    if (message.method === 'thread/realtime/sdp' && typeof message.params?.sdp === 'string' && media) {
      report.sdpReceived = true;
      answerTask = media.answer(message.params.sdp).catch(() => {
        report.status = 'failed'; report.error = 'invalid_sdp_answer'; finishObservation();
      });
    }
    if (message.method === 'thread/realtime/started') {
      report.startedNotification = true;
      report.nativeSessionIdObserved = Boolean(message.params?.realtimeSessionId);
    }
    if (message.method === 'thread/realtime/error') {
      report.error = diagnostic(message.params?.message);
      report.status = 'failed';
      finishObservation();
    }
    if (message.method === 'thread/realtime/closed') finishObservation();
  });
  try {
    await rpc('initialize', { clientInfo: { name: 'calli', title: 'Calli voice probe', version: '0.1.0' }, capabilities: { experimentalApi: true } });
    write({ method: 'initialized', params: {} });
    report.stage = 'account';
    const account = await rpc('account/read', { refreshToken: false });
    report.accountType = account?.account?.type === 'chatgpt' ? 'chatgpt' : account?.account?.type === 'apiKey' ? 'apiKey' : 'unknown';
    report.stage = 'voices';
    const response = await rpc('thread/realtime/listVoices', {});
    const voices = response?.voices;
    if (!Array.isArray(voices?.v1) || !Array.isArray(voices?.v2)) throw new Error('Unsupported voice response');
    report.interfaceAvailable = true;
    report.voiceCounts = { v1: voices.v1.length, v2: voices.v2.length };
    if (!connect) {
      report.status = 'capabilities_checked';
      return report;
    }
    // Never turn this into an API-key fallback, even when one is present in the environment.
    if (report.accountType !== 'chatgpt') {
      report.status = 'blocked'; report.error = 'chatgpt_login_required';
      return report;
    }
    if (webrtc) { report.stage = 'browser'; media = await webrtcProbe(); }
    report.stage = 'thread';
    const result = await rpc('thread/start', { cwd, ephemeral: true, sandbox: 'read-only', approvalPolicy: 'on-request' });
    threadId = result?.thread?.id;
    if (typeof threadId !== 'string' || !threadId || result?.thread?.ephemeral !== true) throw new Error('Unsupported ephemeral thread');
    report.stage = 'realtime';
    await rpc('thread/realtime/start', { threadId, outputModality: report.outputModality, transport: media ? { type: 'webrtc', sdp: media.sdp } : { type: 'websocket' }, ...(protocol ? { version: protocol } : {}) });
    report.startAccepted = true;
    let timer;
    await Promise.race([observation, new Promise(resolve => { timer = setTimeout(resolve, observeMs); })]);
    clearTimeout(timer);
    await answerTask;
    if (media) report.peerConnectionState = await media.state();
    if (!report.error) report.status = processEnded ? 'failed' : 'unverified';
    // An ACK or started notification is not proof that the upstream session or audio works.
    if (processEnded && !report.error) report.error = 'process_closed';
  } catch (error) {
    report.error ??= diagnostic(error); report.status = 'failed';
  } finally {
    if (threadId && !processEnded) {
      try { await rpc('thread/realtime/stop', { threadId }, Math.min(requestTimeoutMs, 2500)); report.stopAcknowledged = true; } catch { /* Still close the owned process. */ }
    }
    child.stdin.end();
    const term = setTimeout(() => { if (!processEnded) child.kill('SIGTERM'); }, 500);
    const kill = setTimeout(() => { if (!processEnded) child.kill('SIGKILL'); }, 2000);
    await closed;
    clearTimeout(term); clearTimeout(kill); lines.close();
    report.processClosed = true;
    await media?.close();
    await rm(cwd, { recursive: true, force: true });
  }
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const args = process.argv.slice(2);
  if (args.includes('--help')) {
    console.log('node scripts/codex-voice-probe.mjs [--connect] [--protocol v1|v2|v3] [--audio-output] [--webrtc]\nDefault: read-only capability probe. --connect: temporary native session; may consume existing plan usage. WebRTC uses a silent synthetic track. No microphone, audible playback or billing verification.');
  } else {
    const options = {};
    for (let i = 0; i < args.length; i++) {
      if (args[i] === '--connect') options.connect = true;
      else if (args[i] === '--audio-output') options.audioOutput = true;
      else if (args[i] === '--webrtc') options.webrtc = true;
      else if (args[i] === '--protocol' && ['v1', 'v2', 'v3'].includes(args[i + 1])) options.protocol = args[++i];
      else { console.error('Unknown or invalid argument. Use --help.'); process.exitCode = 2; break; }
    }
    if (!process.exitCode) {
      const report = await probeVoice(options);
      console.log(JSON.stringify(report, null, 2));
      if (report.status === 'failed' || report.status === 'blocked') process.exitCode = 1;
    }
  }
}
