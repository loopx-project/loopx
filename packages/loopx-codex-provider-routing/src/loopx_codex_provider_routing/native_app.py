"""Packaged macOS Codex profile and App lifecycle IO.

Only native Codex is supported. Optional legacy metadata is read as a profile
reference; no external Personal Apps executable or Python module is imported.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tomllib

from . import native_account_store as accounts

def managed_path(raw, home):
    path = Path(str(raw).replace('~/', str(home) + '/', 1)).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('App state paths must not contain symlinks')
    return path


def route(home, role="gpt"):
    if role != "gpt":
        raise ValueError("Only the native Codex profile is supported")
    home = Path(home)
    metadata = managed_path(home / ".config/personal/apps.json", home)
    if metadata.exists():
        info = metadata.stat()
        if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
            raise ValueError("Native profile metadata is not trusted")
        value = json.loads(metadata.read_text())
        if value.get("version") != 1:
            raise ValueError("Unsupported native profile metadata")
        native = value["apps"]["gpt"]
        if set(native) - {"app", "home", "frontend"}:
            raise ValueError("Unsupported native profile field")
        spec = {key: managed_path(native[key], home) for key in ("home", "frontend")}
        spec["app"] = native.get("app", "")
        spec["legacy_profile"] = True
        spec["lock_root"] = home / ".local/state/personal-apps"
        spec["account_store"] = spec["lock_root"] / "gpt-accounts"
    else:
        spec = {"home": managed_path(os.environ.get("CODEX_HOME") or home / ".codex", home),
                "frontend": managed_path(os.environ.get("CODEX_ELECTRON_USER_DATA_PATH") or home / ".loopx/frontends/codex", home),
                "app": "", "lock_root": home / ".loopx/codex-native-accounts",
                "account_store": home / ".loopx/codex-native-accounts/vault", "legacy_profile": False}
    for key in ("home", "frontend", "lock_root", "account_store"):
        path = managed_path(spec[key], home)
        if not path.is_relative_to(home) or path == home:
            raise ValueError("Native profile state must stay inside its user home")
        spec[key] = path
    if spec["home"] == spec["frontend"] or spec["home"] in spec["frontend"].parents or spec["frontend"] in spec["home"].parents:
        raise ValueError("Codex home and frontend must remain separate")
    return spec


def app_bundle(spec, role, home):
    if spec['app']:
        path = managed_path(spec['app'], home)
        if path.is_dir() and path.suffix == '.app':
            return path
        raise ValueError('Configured App bundle is missing')
    for root in (Path('/Applications'), home / 'Applications'):
        for name in (('ChatGPT.app', 'Codex.app') if spec.get('legacy_profile') else ('Codex.app', 'ChatGPT.app')):
            if (root / name).is_dir():
                return root / name
    raise ValueError('Install the native Codex App before using client restart')


def executable(bundle):
    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    name = info.get('CFBundleExecutable', '')
    if not name or '/' in name or name in ('.', '..'):
        raise ValueError('Invalid App executable metadata')
    path = bundle / 'Contents/MacOS' / name
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('App executable is missing or not executable')
    return path


def clean_env(spec, home=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('CODEX_', 'OPENAI_', 'DEEPSEEK_', 'ELECTRON_', 'DYLD_'))
           and k not in ('NODE_OPTIONS', 'DISABLE_AUTO_UPDATE')}
    env['CODEX_HOME'] = str(spec['home'])
    # Finder does not read zprofile. Keep the caller's active environment first,
    # then add the same installed fallbacks used by the public shell.
    home = Path.home() if home is None else Path(home)
    candidates = [home / '.local/bin', home / '.cargo/bin', home / '.npm-global/bin', home / '.mybin',
                  Path('/opt/homebrew/bin'), Path('/opt/homebrew/sbin'), Path('/usr/local/bin'),
                  Path('/usr/local/sbin'), Path('/opt/homebrew/opt/node@24/bin'),
                  Path('/usr/local/opt/node@24/bin'), Path('/opt/homebrew/opt/python@3.12/libexec/bin'),
                  Path('/usr/local/opt/python@3.12/libexec/bin')]
    paths = [p for p in env.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin').split(os.pathsep) if p]
    for path in candidates:
        if path.is_dir() and str(path) not in paths:
            paths.append(str(path))
    env['PATH'] = os.pathsep.join(paths)
    return env


def owners(exe, frontend, ps_text):
    """Exact frontend ownership, including a Finder root identified by its child."""
    rows = {}
    for line in ps_text.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            rows[int(fields[0])] = (int(fields[1]), fields[2])
    marker = re.compile(re.escape('--user-data-dir=' + str(frontend)) + r'(?=$|\s--|\s*$)')
    roots = {pid for pid, (_, cmd) in rows.items()
             if re.match(r'^/.*?/Contents/MacOS/\S+(?=\s|$)', cmd) and ' --type=' not in cmd}
    found = set()
    for pid, (_, cmd) in rows.items():
        if not marker.search(cmd):
            continue
        current, seen = pid, set()
        while current in rows and current not in seen:
            if current in roots:
                command = rows[current][1]
                if not (command == str(exe) or command.startswith(str(exe) + ' ')):
                    raise ValueError('Frontend is owned by a different App executable')
                found.add(current)
                break
            seen.add(current)
            current = rows[current][0]
        # A frontend owned by a different App build must not launch a second writer.
        if current not in roots:
            raise ValueError('Frontend owner could not be identified; inspect before opening')
    if len(found) > 1:
        raise ValueError('Multiple App processes own this frontend; inspect before opening')
    return next(iter(found), None)


FOCUS = '''ObjC.import("AppKit");
function run(argv) {
  var app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(Number(argv[0]));
  if (!app || app.isTerminated) throw Error("App terminated");
  var target = $.NSAppleEventDescriptor.descriptorWithProcessIdentifier(Number(argv[0]));
  var event = $.NSAppleEventDescriptor.appleEventWithEventClassEventIDTargetDescriptorReturnIDTransactionID(
    0x61657674, 0x72617070, target, -1, 0);
  var error = Ref(); event.sendEventWithOptionsTimeoutError(1, 5, error);
  if (ObjC.unwrap(error[0]) || !app.activateWithOptions(3)) throw Error("Could not focus App");
}'''


def check_provider(spec, role="gpt"):
    if role != "gpt":
        raise ValueError("Only native Codex is supported")
    path = spec["home"] / "config.toml"
    cfg = tomllib.loads(path.read_text()) if path.exists() else {}
    if (cfg.get("model_provider", "openai") != "openai" or cfg.get("model_catalog_json")
            or "openai" in (cfg.get("model_providers") or {})):
        raise ValueError("Account switching requires the built-in OpenAI provider")
    if cfg.get("forced_login_method") == "api":
        accounts.api_endpoint(cfg.get("openai_base_url"))


def account_preflight(home, spec):
    manager = accounts.Accounts(home, spec, None, None, None)
    if manager.pending.exists():
        raise ValueError('Interrupted account operation; run codex app recover with GPT closed')
    if manager.state():
        manager.selected()  # Compare identity only; refreshed tokens may differ.


def account_idle(home, spec, read_only_owners=()):
    """Never kill clients; explicit service exceptions must remain read-only."""
    exe = executable(app_bundle(spec, 'gpt', home))
    if running(exe, spec):
        raise ValueError('Quit the GPT App normally before switching; DS can remain open')
    if not spec['home'].exists():
        return
    result = subprocess.run(['/usr/sbin/lsof', '-Fpcfa', '+d', str(spec['home'])],
                            capture_output=True, text=True, timeout=10)
    if result.returncode not in (0, 1):
        raise ValueError('Cannot verify GPT home ownership; no account was changed')
    owners, owner, descriptor = [], None, None
    for line in result.stdout.splitlines():
        if line.startswith('p') and line[1:].isdigit():
            owner = {'pid': int(line[1:]), 'command': 'unknown', 'files': []}
            owners.append(owner)
            descriptor = None
        elif line.startswith('c') and owner is not None:
            owner['command'] = line[1:]
        elif line.startswith('f') and owner is not None:
            # lsof marks some descriptors with a leading '*' (duplicate handles);
            # keep the numeric identity so read-only owners can still be allowed.
            descriptor = {'fd': line[1:].lstrip('*'), 'access': None}
            owner['files'].append(descriptor)
        elif line.startswith('a') and descriptor is not None:
            descriptor['access'] = line[1:]
        elif line:
            raise ValueError('Cannot parse GPT home ownership; no account was changed')
    blocked = [owner for owner in owners if not (
        owner['pid'] in read_only_owners and owner['files'] and all(
            item['fd'].isdigit() and item['access'] == 'r' for item in owner['files']))]
    if blocked:
        details = ', '.join(f"{owner['pid']} ({owner['command']})" for owner in blocked)
        raise ValueError('GPT home is still open by an App/CLI process or filesystem service; '
                         f'close the owning client before switching: {details}. '
                         'New-account enrollment without --current can continue while the App is open.')


def running(exe, spec):
    ps = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,command='],
                        capture_output=True, text=True, check=True).stdout
    return owners(exe, spec['frontend'], ps)


def open_app(home, role, files, dry_run=False, *, spec=None):
    if role != "gpt" or files:
        raise ValueError("Native Codex launch accepts no workspace arguments")
    spec = route(home, role) if spec is None else spec
    def preflight():
        check_provider(spec, role)
        account_preflight(home, spec)
    return open_codex(home, spec, preflight=preflight, role=role, dry_run=dry_run)


def open_codex(home, spec, *, preflight, role="gpt", dry_run=False):
    """Shared Codex process launch; callers retain credential/provider admission."""
    bundle = app_bundle(spec, role, home)
    exe = executable(bundle)
    preflight()
    pid = running(exe, spec)
    result = {'action': 'focus' if pid else 'launch', 'role': role,
              'home': str(spec['home']), 'frontend': str(spec['frontend']), 'pid': pid}
    if dry_run:
        return dict(result, preview=True)
    lock_dir = managed_path(spec['lock_root'], home)
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = managed_path(lock_dir / 'launch.lock', home)
    with open(lock_path, 'a', opener=lambda p, flags: os.open(p, flags, 0o600)) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        preflight()
        pid = running(exe, spec)
        if pid:
            subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-', str(pid)],
                           input=FOCUS, text=True, check=True, capture_output=True)
            return dict(result, action='focused', pid=pid)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                       check=True, capture_output=True)
        spec['frontend'].mkdir(mode=0o700, parents=True, exist_ok=True)
        env = clean_env(spec, home)
        env['CODEX_ELECTRON_USER_DATA_PATH'] = str(spec['frontend'])
        child = subprocess.Popen([str(exe), '--user-data-dir=' + str(spec['frontend'])],
                                 env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        # Hold the lock until the child owns its frontend, preventing a double-click race.
        import time
        for _ in range(50):
            if child.poll() is not None:
                raise ValueError('App exited during startup; inspect the App locally')
            if running(exe, spec) == child.pid:
                return dict(result, action='launch-requested', pid=child.pid)
            time.sleep(0.1)
        raise ValueError('App ownership was not confirmed within five seconds; inspect before retrying')
