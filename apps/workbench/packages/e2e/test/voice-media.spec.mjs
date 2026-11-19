import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { launchBrowser } from '../harness.mjs';

test('voice media negotiates local Chromium peers and plays a streamless audio track', { timeout: 30_000 }, async () => {
  // No daemon, upstream service or real microphone. Two local peers use synthetic audio only.
  const bundle = await build({
    entryPoints: [fileURLToPath(new URL('../../web/src/voice-media.ts', import.meta.url))],
    bundle: true, write: false, format: 'iife', globalName: 'VoiceMedia', platform: 'browser',
  });
  const server = createServer((_req, res) => { res.end('<!doctype html><button id="start">Start local media test</button>'); });
  let browser;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await launchBrowser();
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(() => {
      document.querySelector('#start').onclick = () => {
        window.result = (async () => {
          const audio = new AudioContext();
          const oscillator = audio.createOscillator();
          const gain = audio.createGain(); gain.gain.value = .15;
          oscillator.connect(gain); oscillator.start();
          const local = audio.createMediaStreamDestination();
          const remote = audio.createMediaStreamDestination();
          gain.connect(local); gain.connect(remote);
          const sink = new Audio(); sink.muted = true;
          const peer = new RTCPeerConnection({ iceServers: [] });
          const controller = new VoiceMedia.VoiceMediaController({
            env: { mediaDevices: { getUserMedia: async () => local.stream } }, audioSink: sink,
          });
          try {
            await audio.resume();
            const offer = await controller.prepare();
            await peer.setRemoteDescription({ type: 'offer', sdp: offer });
            // Deliberately omit stream association: RTCTrackEvent.streams will be empty.
            peer.addTrack(remote.stream.getAudioTracks()[0]);
            await peer.setLocalDescription(await peer.createAnswer());
            if (peer.iceGatheringState !== 'complete') await new Promise((resolve, reject) => {
              const timer = setTimeout(() => reject(new Error('Local peer ICE timeout')), 5000);
              peer.addEventListener('icegatheringstatechange', () => {
                if (peer.iceGatheringState === 'complete') { clearTimeout(timer); resolve(); }
              });
            });
            await controller.acceptAnswer(peer.localDescription.sdp);
            const deadline = Date.now() + 5000;
            while (Date.now() < deadline && controller.state !== 'error'
              && !(controller.state === 'connected' && sink.readyState >= 2 && !sink.paused)) {
              await new Promise(resolve => setTimeout(resolve, 20));
            }
            const result = { state: controller.state, error: controller.error?.code,
              stream: sink.srcObject instanceof MediaStream, playing: !sink.paused && sink.readyState >= 2 };
            const meterDeadline = Date.now() + 2000;
            let levels;
            do { levels = controller.levels(); await new Promise(resolve => setTimeout(resolve, 20)); }
            while (Date.now() < meterDeadline && (levels.input <= .01 || levels.output <= .01));
            result.inputMeter = levels.input > .01;
            result.outputMeter = levels.output > .01;
            controller.setMuted(true);
            result.mutedMeter = controller.levels().input === 0;
            result.muted = local.stream.getAudioTracks().every(track => !track.enabled);
            controller.dispose();
            result.closed = controller.state === 'closed' && local.stream.getTracks().every(track => track.readyState === 'ended') && sink.srcObject === null;
            return result;
          } finally {
            controller.dispose(); peer.close(); oscillator.stop();
            for (const track of [...local.stream.getTracks(), ...remote.stream.getTracks()]) track.stop();
            await audio.close();
          }
        })();
      };
    });
    await page.click('#start');
    const result = await page.evaluate(() => window.result);
    assert.deepEqual(result, { state: 'connected', error: undefined, stream: true, playing: true, inputMeter: true, outputMeter: true, mutedMeter: true, muted: true, closed: true });
  } finally {
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
