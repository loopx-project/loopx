#!/usr/bin/env node
// A stand-in for systemctl in restart.sh tests. It logs one line per restart call to
// systemctl.log (in $CALLI_FAKE_STATE), which tests assert on: "restart" lines show when (and
// whether) the restart happened.
//   systemctl-fake cat calli                  exits 1 unless $STATE/service-dir exists
//   systemctl-fake show -p K --value calli    WorkingDirectory -> the path inside
//                                             $STATE/service-dir (a file), MainPID -> 0
//   systemctl-fake restart ... calli          logs, then exits with $STATE/restart-exit or 0
//   anything else (daemon-reload, status...)  exits 0
import { appendFileSync, existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const state = process.env.CALLI_FAKE_STATE ?? here;
const args = process.argv.slice(2);
const exit = (code = 0) => process.exit(code);

if (args.includes('cat')) exit(existsSync(join(state, 'service-dir')) ? 0 : 1);

if (args.includes('show')) {
  const key = args[args.indexOf('-p') + 1];
  if (key === 'WorkingDirectory') console.log(resolve(readFileSync(join(state, 'service-dir'), 'utf8').trim()));
  if (key === 'Environment') console.log(`CALLI_ENV_FILE=${join(state, 'env')}`);
  if (key === 'MainPID') console.log('0');
  exit();
}

if (args.includes('restart')) {
  appendFileSync(join(state, 'systemctl.log'), `restart ${args.join(' ')}\n`);
  const fail = join(state, 'restart-exit');
  exit(existsSync(fail) ? Number(readFileSync(fail, 'utf8').trim()) : 0);
}

exit();
