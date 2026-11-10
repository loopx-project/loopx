"""Packaged native Codex account vault, transactions and recovery.

This is the single credential writer used by the optional native provider.
Legacy vaults retain their original binding and locks; no history is migrated.
"""
import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
from urllib.parse import urlsplit
import uuid

API_KEY_SLOTS_VERSION = 1
LEGACY_RELAY_PROVIDERS = frozenset({
    'openai_retry20',
    'loopx_http',
    'loopx_account_api',
    'loopx_host_chatgpt_http',
})


def api_endpoint(value):
    if not isinstance(value, str) or not re.fullmatch(r'http://127\.0\.0\.1:[0-9]+/v1', value):
        raise ValueError('API fallback requires an explicit loopback Responses endpoint')
    port = int(value.split(':')[2].split('/')[0])
    if not 1024 <= port <= 65535:
        raise ValueError('API fallback requires an unprivileged loopback port')
    return value


def gateway_endpoint(value):
    """Accept a Responses API gateway without allowing credentials in the URL."""
    try:
        parts = urlsplit(value) if isinstance(value, str) else None
        port = parts.port if parts is not None else None
    except ValueError:
        parts = None
        port = None
    if (parts is None or parts.scheme not in ('http', 'https') or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or port == 0):
        raise ValueError('API gateway requires a clean HTTP(S) Responses endpoint')
    if parts.scheme == 'http' and parts.hostname not in ('127.0.0.1', '::1', 'localhost'):
        raise ValueError('Unencrypted API gateways must stay on the loopback interface')
    return value


def provider_route(cfg):
    """Return the selected provider after validating launch-safe configuration."""
    providers = cfg.get('model_providers') or {}
    if not isinstance(providers, dict):
        raise ValueError('Invalid Codex provider definitions')
    if 'openai' in providers:
        raise ValueError('A custom provider must not shadow the built-in OpenAI provider')
    provider = cfg.get('model_provider', 'openai')
    if not isinstance(provider, str) or not provider:
        raise ValueError('Invalid Codex provider selection')
    if provider == 'openai':
        endpoint = cfg.get('openai_base_url')
        if endpoint is not None:
            gateway_endpoint(endpoint)
        return provider
    definition = providers.get(provider)
    if not isinstance(definition, dict):
        raise ValueError('Selected Codex provider is not a configured API gateway')
    if definition.get('wire_api', 'responses') != 'responses':
        raise ValueError('API gateways must use the Responses protocol')
    gateway_endpoint(definition.get('base_url'))
    return provider


def auth_kind(raw):
    obj = json.loads(raw)
    return 'api' if obj.get('auth_mode') == 'apikey' else 'subscription'


def external_api_auth(raw):
    """Direct API-key login owned by Codex, not by the managed relay vault."""
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return False
        key = obj.get('OPENAI_API_KEY')
        return (obj.get('auth_mode') == 'apikey' and isinstance(key, str)
                and bool(key) and not key.startswith('sk-loopx-relay-')
                and not any(c.isspace() for c in key) and not obj.get('tokens'))
    except (TypeError, ValueError):
        return False


def checked(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Account paths must not contain symlinks')
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def private_dir(path):
    checked(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    for created in missing:
        os.chmod(created, 0o700)
    os.chmod(path, 0o700)


def atomic(path, data):
    checked(path)
    private_dir(path.parent)
    fd, name = tempfile.mkstemp(prefix='.account-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def encoded(value):
    return (json.dumps(value, indent=2) + '\n').encode()


@contextlib.contextmanager
def credential_lock(home, shared=False, *, lock_root=None):
    folder = checked(Path(lock_root) if lock_root is not None else Path(home) / '.loopx/codex-native-accounts')
    private_dir(folder)
    path = checked(folder / 'account.lock')
    with open(path, 'a', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        yield stream


def read_private(path):
    path = checked(path)
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('Account cache/state must be a regular private file (chmod 600 locally)')
    return path.read_bytes()


def identity(raw):
    """Metadata comparison only; official Codex owns verification and refresh."""
    try:
        obj = json.loads(raw)
        if obj.get('auth_mode') == 'apikey':
            key = obj.get('OPENAI_API_KEY')
            if (not isinstance(key, str) or not key.startswith('sk-loopx-relay-')
                    or not 32 <= len(key) <= 512 or any(c.isspace() for c in key) or obj.get('tokens')):
                raise ValueError()
            provider = obj.get('loopx_model_provider')
            if provider is not None and (not isinstance(provider, str) or not provider
                                         or any(c.isspace() for c in provider)):
                raise ValueError()
            endpoint = api_endpoint(obj.get('loopx_api_base_url'))
            return digest(encoded(['apikey', key, endpoint]))
        tokens = obj['tokens']
        if obj.get('auth_mode') not in (None, 'chatgpt') or obj.get('OPENAI_API_KEY'):
            raise ValueError()
        if any(not isinstance(tokens.get(k), str) or not tokens[k]
               for k in ('id_token', 'access_token', 'refresh_token')):
            raise ValueError()
        parts = tokens['id_token'].split('.')
        if len(parts) != 3:
            raise ValueError()
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
        subject = claims.get('sub')
        if not isinstance(subject, str) or not subject:
            raise ValueError()
        account = tokens.get('account_id') or ''
        if not isinstance(account, str):
            raise ValueError()
        return digest(encoded([subject, account]))
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        raise ValueError('Invalid native account cache; inspect the local login or relay configuration') from None


def routed_auth(raw, provider):
    """Bind a relay credential to the provider restored when its slot is selected."""
    if auth_kind(raw) != 'api' or not isinstance(provider, str) or not provider:
        raise ValueError('Invalid API gateway account route')
    obj = json.loads(raw)
    obj['loopx_model_provider'] = provider
    return encoded(obj)


def managed_relay_provider(cfg, provider, endpoint):
    """True when an API slot provider is the built-in or legacy loopback relay route."""
    if not provider or provider == 'openai' or provider in LEGACY_RELAY_PROVIDERS:
        return True
    providers = cfg.get('model_providers')
    definition = providers.get(provider) if isinstance(providers, dict) else None
    return isinstance(definition, dict) and definition.get('base_url') == endpoint


def routed_api_auth(raw, cfg, current_provider, *, outgoing=False):
    """Preserve explicit custom gateways while migrating legacy relay providers to openai."""
    if auth_kind(raw) != 'api':
        return raw
    obj = json.loads(raw)
    endpoint = api_endpoint(obj['loopx_api_base_url'])
    provider = current_provider if outgoing and current_provider != 'openai' else (
        obj.get('loopx_model_provider') or current_provider)
    if managed_relay_provider(cfg, provider, endpoint):
        provider = 'openai'
    return routed_auth(raw, provider)


def toml_string(value):
    if not isinstance(value, str) or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value):
        raise ValueError('Invalid TOML setting value')
    return json.dumps(value, ensure_ascii=False)


def file_config(raw, auth=None):
    try:
        text = raw.decode()
        cfg = tomllib.loads(text)
    except (ValueError, UnicodeError):
        raise ValueError('Invalid GPT config; repair locally without printing its contents') from None
    current_provider = provider_route(cfg)
    # A profile can replace the checked provider at runtime, so account switching
    # cannot safely infer which route the credential transaction owns.
    if cfg.get('profiles') or cfg.get('profile'):
        raise ValueError('Account switching does not support provider profile overrides')
    if cfg.get('cli_auth_credentials_store') not in (None, 'auto', 'file'):
        raise ValueError('Explicit keyring/ephemeral storage is preserved; use native sign-in or review file storage locally')
    if cfg.get('forced_login_method') not in (None, 'chatgpt', 'api'):
        raise ValueError('Unsupported account login method')
    mode = 'api' if auth is not None and auth_kind(auth) == 'api' else 'chatgpt'
    target_provider = 'openai'
    endpoint = None
    if mode == 'api':
        identity(auth)
        auth_obj = json.loads(auth)
        endpoint = api_endpoint(auth_obj['loopx_api_base_url'])
        stored_provider = auth_obj.get('loopx_model_provider', 'openai')
        target_provider = 'openai' if managed_relay_provider(cfg, stored_provider, endpoint) else stored_provider
        candidate = dict(cfg, model_provider=target_provider)
        provider_route(candidate)
    # Subscription and managed relay slots use the built-in provider identity so
    # existing Codex sessions stay resumable across account/fallback transitions.
    # Explicit custom Responses gateways restore their bound provider definition.
    section = re.search(r'^\s*\[', text, re.M)
    head, tail = (text[:section.start()], text[section.start():]) if section else (text, '')
    settings = [('cli_auth_credentials_store', 'file'), ('forced_login_method', mode)]
    if target_provider != current_provider:
        settings.insert(0, ('model_provider', target_provider))
    if mode == 'api' and target_provider == 'openai':
        if cfg.get('openai_base_url') and cfg.get('forced_login_method') != 'api':
            raise ValueError('An existing OpenAI base URL must not be replaced by API fallback')
        if cfg.get('openai_base_url'):
            api_endpoint(cfg['openai_base_url'])
        settings.append(('openai_base_url', endpoint))
    elif mode == 'chatgpt' and cfg.get('openai_base_url'):
        api_endpoint(cfg['openai_base_url'])
        pattern = r'^openai_base_url\s*=.*(?:\n|$)'
        if len(re.findall(pattern, head, re.M)) != 1:
            raise ValueError('Nonstandard API base URL syntax requires local review')
        head = re.sub(pattern, '', head, flags=re.M)
    for key, value in settings:
        pattern = rf'^{key}\s*=.*$'
        rendered = f'{key} = {toml_string(value)}'
        if key in cfg:
            if len(re.findall(pattern, head, re.M)) != 1:
                raise ValueError('Nonstandard auth setting syntax requires local review')
            head = re.sub(pattern, lambda _: rendered, head, flags=re.M)
        else:
            head = f'{rendered}\n' + head
    converted = (head + tail).encode()
    try:
        updated = tomllib.loads(converted.decode())
    except (ValueError, UnicodeError):
        raise ValueError('Nonstandard auth setting syntax requires local review') from None
    expected_base_url = endpoint if mode == 'api' and target_provider == 'openai' else (
        None if mode == 'chatgpt' else cfg.get('openai_base_url'))
    if (provider_route(updated) != target_provider
            or updated.get('cli_auth_credentials_store') != 'file'
            or updated.get('forced_login_method') != mode
            or updated.get('openai_base_url') != expected_base_url):
        raise ValueError('Nonstandard auth setting syntax requires local review')
    return converted


class Accounts:
    def __init__(self, home, spec, idle, login_cli, env):
        self.home, self.spec, self.idle = Path(home), spec, idle
        self.login_cli, self.env = login_cli, env
        self.root = checked(Path(spec.get('account_store', self.home / '.loopx/codex-native-accounts/vault')))
        self.lock_root = checked(Path(spec.get('lock_root', self.home / '.loopx/codex-native-accounts')))
        self.binding = {'home': str(spec['home']), 'frontend': str(spec['frontend'])}
        self.config = checked(spec['home'] / 'config.toml')
        self.auth = checked(spec['home'] / 'auth.json')
        self.active = checked(self.root / 'active.json')
        self.pending = checked(self.root / 'pending.json')
        self.binding_file = checked(self.root / 'binding.json')
        if self.binding_file.exists():
            try:
                matches = json.loads(read_private(self.binding_file)) == self.binding
            except ValueError:
                matches = False
            if not matches:
                raise ValueError('Account vault belongs to a different GPT route; inspect local settings')
        if self.active.exists():
            self.state()  # A changed route must not silently reuse another route's vault.

    def slot(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,23}', name):
            raise ValueError('Account slots use lowercase letters, digits and hyphens')
        return checked(self.root / 'slots' / name / 'auth.json')

    def state(self):
        if not self.active.exists():
            return None
        try:
            obj = json.loads(read_private(self.active))
            self.slot(obj['slot'])
            if obj['binding'] != self.binding or not re.fullmatch(r'[a-f0-9]{64}', obj['identity']):
                raise ValueError()
            return obj
        except (KeyError, TypeError, ValueError):
            raise ValueError('Account selection is invalid or belongs to a different GPT route') from None

    @contextlib.contextmanager
    def lock(self, *, enrollment=False):
        # Same lock as App launch: cooperating launch/switch processes cannot race.
        folder = checked(self.lock_root)
        private_dir(folder)
        path = checked(folder / 'launch.lock')
        with open(path, 'a', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with credential_lock(self.home, shared=enrollment, lock_root=self.lock_root):
                private_dir(self.root)
                yield

    def selected(self):
        state = self.state()
        if state:
            raw = read_private(self.auth)
            current = identity(raw)
            if current != state['identity']:
                adopted = self._adopt(current)
                if adopted is not None:
                    return adopted, raw
                raise ValueError('GPT login changed outside the slot manager; inspect before overwriting any account')
            return state, raw
        if self.auth.exists():
            raw = read_private(self.auth)
            adopted = self._adopt(identity(raw))
            if adopted is not None:
                return adopted, raw
            raise ValueError('Register the current GPT login first: codex app enroll a --current')
        return None, None

    def _adopt(self, current):
        """Adopt the home login when it matches exactly one stored slot.

        An operator or another tool can restore a slot's credentials directly,
        for example when returning from the API route after an outage. Only a
        unique identity match is adopted; an unknown or ambiguous login still
        requires inspection.
        """
        folder = checked(self.root / 'slots')
        if not folder.is_dir() or self.pending.exists():
            return None
        try:
            # Metadata adoption must not race a credential transaction. Shared
            # credential access does not rewrite or refresh the current login.
            with self.lock(enrollment=True):
                raw = read_private(self.auth)
                if self.pending.exists() or identity(raw) != current:
                    return None
                config = self.config.read_bytes() if self.config.exists() else b''
                expected = tomllib.loads(file_config(config, raw).decode())
                actual = tomllib.loads(config.decode())
                if (actual.get('model_provider', 'openai') != expected.get('model_provider', 'openai')
                        or actual.get('openai_base_url') != expected.get('openai_base_url')
                        or actual.get('forced_login_method') not in (None, expected['forced_login_method'])):
                    return None
                # Use the same complete, validated slot inventory as status;
                # an unreadable slot is not proof that the match is unique.
                matches = [entry.name for entry in sorted(folder.iterdir())
                           if identity(read_private(self.slot(entry.name))) == current]
                if len(matches) != 1:
                    return None
                state = {'slot': matches[0], 'identity': current, 'binding': self.binding}
                atomic(self.active, encoded(state))
                return state
        except BlockingIOError:
            return None  # The existing owner retries after its lock releases.

    def targets(self, key):
        if key in ('auth', 'config', 'active', 'binding'):
            return checked({'auth': self.auth, 'config': self.config, 'active': self.active,
                            'binding': self.binding_file}[key])
        if isinstance(key, str) and key.startswith('slot:'):
            return self.slot(key[5:])
        raise ValueError('Invalid account transaction target')

    def restore(self, journal):
        """Validate every target and snapshot before restoring any of them."""
        try:
            if (type(journal['version']) is not int or journal['version'] != 1
                    or journal['binding'] != self.binding
                    or not re.fullmatch(r'[a-f0-9]{32}', journal['id'])
                    or not isinstance(journal['files'], list)):
                raise ValueError()
            backup = checked(self.root / 'backups' / journal['id'])
            plan, seen = [], set()
            for index, row in enumerate(journal['files']):
                if (not isinstance(row['existed'], bool)
                        or not re.fullmatch(r'[a-f0-9]{64}', row['after_hash'])):
                    raise ValueError()
                path = self.targets(row['key'])
                if path in seen:
                    raise ValueError()
                seen.add(path)
                old = read_private(backup / str(index)) if row['existed'] else None
                if (digest(old) if old is not None else None) != row['before_hash']:
                    raise ValueError()
                current = digest(path.read_bytes()) if path.exists() else None
                if current not in (row['before_hash'], row['after_hash']):
                    raise ValueError()
                plan.append((path, old))
            if not plan:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ValueError('Recovery rejected changed files or an invalid account snapshot; inspect locally') from None
        for path, old in reversed(plan):
            if old is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, old)

    def transaction(self, updates, operation):
        if self.pending.exists():
            raise ValueError('Interrupted account operation; run codex app recover with GPT closed')
        if not self.binding_file.exists():
            updates = [('binding', encoded(self.binding)), *updates]
        ident = uuid.uuid4().hex
        backup = checked(self.root / 'backups' / ident)
        private_dir(backup)
        rows = []
        for index, (key, raw) in enumerate(updates):
            path = self.targets(key)
            existed = path.exists()
            old = path.read_bytes() if existed else None
            if existed:
                atomic(backup / str(index), old)
            rows.append({'key': key, 'existed': existed,
                         'before_hash': digest(old) if existed else None, 'after_hash': digest(raw)})
        journal = {'version': 1, 'id': ident, 'binding': self.binding, 'operation': operation, 'files': rows}
        atomic(backup / 'manifest.json', encoded(journal))
        atomic(self.pending, encoded(journal))
        try:
            self.idle()  # Recheck immediately before the first mutation.
            for key, raw in updates:
                path = self.targets(key)
                atomic(path, raw)
                if path.read_bytes() != raw:
                    raise ValueError('Account writeback verification failed')
            atomic(self.root / 'last.json', encoded(journal))
        except BaseException:
            self.restore(journal)
            self.pending.unlink()
            raise
        self.pending.unlink()
        return {'operation': operation, 'backup': str(backup), 'history_untouched': True}

    def status(self, *, adopt_current=True):
        """Read selection; callers without repair authority can disable adoption."""
        names = []
        kinds = {}
        folder = checked(self.root / 'slots')
        if folder.exists():
            for child in sorted(folder.iterdir()):
                identity(read_private(self.slot(child.name)))
                names.append(child.name)
                kinds[child.name] = auth_kind(read_private(self.slot(child.name)))
        state = self.state()
        matches = None
        if self.auth.exists():
            try:
                current = identity(read_private(self.auth))
                if adopt_current and (not state or current != state['identity']):
                    state = self._adopt(current) or state
                matches = current == state['identity'] if state else None
            except ValueError:
                matches = False if state else None
        return {'slots': names, 'slot_kinds': kinds, 'active_slot': state['slot'] if state else None,
                'current_login_matches': matches, 'recovery_required': self.pending.exists(),
                'home': str(self.spec['home']), 'frontend': str(self.spec['frontend']),
                'live_authentication_verified': False}

    def enroll_api(self, name, *, api_key, base_url):
        """Enroll an explicit local relay credential without selecting it."""
        endpoint = api_endpoint(base_url)
        provider = 'openai'
        if self.config.exists():
            cfg = tomllib.loads(self.config.read_text())
            current = provider_route(cfg)
            provider = 'openai' if managed_relay_provider(cfg, current, endpoint) else current
        raw = encoded({'auth_mode': 'apikey', 'OPENAI_API_KEY': api_key,
                       'loopx_api_base_url': endpoint,
                       'loopx_model_provider': provider})
        ident = identity(raw)
        target = self.slot(name)
        with self.lock(enrollment=True):
            if self.pending.exists():
                raise ValueError('Recover an interrupted operation before enrollment')
            if target.exists():
                if read_private(target) == raw:
                    return {'slot': name, 'activated': False, 'credentials_printed': False}
                raise ValueError('Existing slots are never overwritten')
            if any(identity(read_private(self.slot(other))) == ident for other in self.status()['slots']):
                raise ValueError('That credential is already registered')
            if not self.binding_file.exists():
                atomic(self.binding_file, encoded(self.binding))
            atomic(target, raw)
            return {'slot': name, 'activated': False, 'credentials_printed': False}

    def enroll(self, name, current=False):
        target = self.slot(name)
        if current:
            self.idle()
        # Staged login never reads or changes the active credentials. Share the
        # CLI lease, but serialize slot creation and switching with launch.lock.
        with self.lock(enrollment=not current):
            if self.pending.exists() or target.exists():
                raise ValueError('Recover any interrupted operation first; existing slots are never overwritten')
            if current:
                converted = file_config(self.config.read_bytes() if self.config.exists() else b'')
                raw = read_private(self.auth)
                ident = identity(raw)
                state = self.state()
                if state and state['identity'] != ident:
                    raise ValueError('The selected account changed externally; inspect before enrollment')
            else:
                private_dir(self.root / 'login-staging')
                with tempfile.TemporaryDirectory(dir=self.root / 'login-staging', prefix='login-') as temp:
                    staging = Path(temp)
                    atomic(staging / 'config.toml', b'cli_auth_credentials_store="file"\nforced_login_method="chatgpt"\n')
                    command = [self.login_cli(), 'login']
                    if subprocess.run(command, env=self.env(dict(self.spec, home=staging))).returncode:
                        raise ValueError('Official login did not complete; the active GPT account is unchanged')
                    raw = read_private(staging / 'auth.json')
                    ident = identity(raw)
            existing = self.status()['slots']
            if any(identity(read_private(self.slot(other))) == ident for other in existing):
                raise ValueError('That ChatGPT account is already registered; select its existing slot')
            if current:
                updates = [('slot:' + name, raw), ('config', converted),
                           ('active', encoded({'slot': name, 'identity': ident, 'binding': self.binding}))]
                result = self.transaction(updates, 'enroll-current')
            else:
                # Only an immutable new slot is published. Do not create a live
                # recovery journal or replace the last credential-switch backup.
                if not self.binding_file.exists():
                    atomic(self.binding_file, encoded(self.binding))
                atomic(target, raw)
                result = {'operation': 'enroll', 'history_untouched': True}
            return dict(result, slot=name, activated=current, credentials_printed=False)

    def switch(self, name, dry_run=False):
        target = self.slot(name)
        self.idle()
        if self.pending.exists():
            raise ValueError('Interrupted account operation; run codex app recover with GPT closed')
        raw = read_private(target)
        config_raw = self.config.read_bytes() if self.config.exists() else b''
        cfg = tomllib.loads(config_raw.decode()) if config_raw else {}
        current_provider = provider_route(cfg)
        raw = routed_api_auth(raw, cfg, current_provider)
        ident = identity(raw)
        converted = file_config(config_raw, raw)
        state, outgoing = self.selected()
        if dry_run:
            return {'preview': True, 'slot': name, 'home': str(self.spec['home']),
                    'frontend': str(self.spec['frontend']), 'history_untouched': True}
        with self.lock():
            self.idle()
            # Reread under the lock: retain refresh-token rotation from the last use.
            state, outgoing = self.selected()
            raw = outgoing if state and state['slot'] == name else read_private(target)
            config_raw = self.config.read_bytes() if self.config.exists() else b''
            cfg = tomllib.loads(config_raw.decode()) if config_raw else {}
            current_provider = provider_route(cfg)
            if state and auth_kind(outgoing) == 'api':
                outgoing = routed_api_auth(outgoing, cfg, current_provider, outgoing=True)
            raw = routed_api_auth(raw, cfg, current_provider)
            ident = identity(raw)
            converted = file_config(config_raw, raw)
            updates = []
            if state:
                updates.append(('slot:' + state['slot'], outgoing))
            if not state or state['slot'] != name:
                updates.append(('slot:' + name, raw))
            updates += [('config', converted), ('auth', raw),
                        ('active', encoded({'slot': name, 'identity': ident, 'binding': self.binding}))]
            return dict(self.transaction(updates, 'switch'), slot=name,
                        home=str(self.spec['home']), frontend=str(self.spec['frontend']))

    def recover(self, rollback=False):
        self.idle()
        with self.lock():
            path = self.root / ('last.json' if rollback else 'pending.json')
            if rollback and self.pending.exists():
                raise ValueError('Recover the interrupted operation before rolling back a completed operation')
            if not path.exists():
                raise ValueError('No matching account recovery snapshot')
            try:
                journal = json.loads(read_private(path))
            except ValueError:
                raise ValueError('Invalid private recovery journal') from None
            self.idle()
            self.restore(journal)
            path.unlink()
            return {'action': 'rolled-back' if rollback else 'recovered', 'history_untouched': True}
