// A tiny native installation with the same guarded output seams, not vendor code.
import { createHash } from 'node:crypto';
import { chmodSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export function fakeDoubaoBundle(bundle) {
  const router = bundle === 'entry-cli-router';
  const printer = router ? 'I=e=>{i.flush(),n.stdout' : 'm=e=>{r.flush(),i.stdout';
  const session = router ? 'f' : 'p';
  const method = router ? 'resolveSettings(e,t){return G({im:this.im,botId:this.botId},e,t)}' : 'resolveSettings(e,t){return et({im:this.im,botId:this.botId},e,t)}';
  const models = `class Chat{${method}};if("list"===o.kind)return ${router ? 'p' : 'b'}(e,t);const endpoint="/alice/slot/action_bar_v3/brief_list";`;
  return `${models}const request={output_format:"text"}; switch(format){case"stream-json":let ${printer}(JSON.stringify(e))};let init={subtype:"init",session_id:${session}};let events={onEvent:e=>{switch(e.type){case"session.created":case"session.bound":break}}};case"json":break}`;
}

export function writeFakeDoubaoManifest(root) {
  const paths = ['doubao', 'resources/js_app/entry-cli-router/main.js', 'resources/js_app/entry-cli-chat/main.js', 'resources/native-resource.txt'];
  const manifest = { version: '2.31.2', target: 'linux-x64', entrypoint: 'doubao', files: paths.map(path => {
    const bytes = readFileSync(join(root, path));
    return { path, size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') };
  }) };
  writeFileSync(join(root, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  return manifest;
}

export function createFakeDoubaoRuntime(root) {
  mkdirSync(root, { recursive: true });
  const fixture = fileURLToPath(new URL('./fake-doubao.mjs', import.meta.url));
  const bin = join(root, 'doubao');
  writeFileSync(bin, `#!/bin/sh\nFAKE_DOUBAO_RUNTIME="$0" exec "${process.execPath}" "${fixture}" "$@"\n`);
  chmodSync(bin, 0o755);
  for (const bundle of ['entry-cli-router', 'entry-cli-chat']) {
    const path = join(root, 'resources/js_app', bundle, 'main.js');
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, fakeDoubaoBundle(bundle));
  }
  writeFileSync(join(root, 'resources/native-resource.txt'), 'Keep all untouched native runtime resources.\n');
  writeFakeDoubaoManifest(root);
  return bin;
}
