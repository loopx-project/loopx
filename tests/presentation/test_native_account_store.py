"""Synthetic login caches only; never load a developer's real accounts."""
import base64
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

import pytest

if os.name != "posix":
    pytest.skip("Native account IO requires POSIX locks", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/loopx-codex-provider-routing/src"))
accounts = importlib.import_module("loopx_codex_provider_routing.native_account_store")
apps = importlib.import_module("loopx_codex_provider_routing.native_app")


def fake_login(subject, rotation='initial'):
    claims = base64.urlsafe_b64encode(json.dumps({'sub': 'synthetic-' + subject}).encode()).decode().rstrip('=')
    return accounts.encoded({'auth_mode': 'chatgpt', 'tokens': {
        'id_token': 'synthetic.' + claims + '.not-a-signature',
        'access_token': 'synthetic-access-' + rotation,
        'refresh_token': 'synthetic-refresh-' + rotation, 'account_id': 'synthetic-personal'}})


class AccountTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve() / 'home with spaces'
        self.home.mkdir()
        with patch.dict(os.environ, {"CODEX_HOME": str(self.home / ".codex"),
                                    "CODEX_ELECTRON_USER_DATA_PATH": str(self.home / "frontend")}):
            self.spec = apps.route(self.home, 'gpt')
        self.spec['home'].mkdir(parents=True, mode=0o700)
        config = self.spec['home'] / 'config.toml'
        config.write_text('model_provider="openai"\n')
        config.chmod(0o600)
        self.idle = Mock()
        self.manager = accounts.Accounts(self.home, self.spec, self.idle,
                                         lambda: '/synthetic/bin/codex', apps.clean_env)
        self.a, self.b = fake_login('a'), fake_login('b')
        accounts.atomic(self.manager.auth, self.a)

    def current(self):
        return self.manager.enroll('a', current=True)

    def enroll_b(self):
        def login(cmd, env):
            self.assertEqual(cmd, ['/synthetic/bin/codex', 'login'])
            stage = Path(env['CODEX_HOME'])
            self.assertNotEqual(stage, self.spec['home'])
            self.assertEqual((stage / 'config.toml').read_text(),
                             'cli_auth_credentials_store="file"\nforced_login_method="chatgpt"\n')
            accounts.atomic(stage / 'auth.json', self.b)
            return subprocess.CompletedProcess(cmd, 0)
        with patch.object(accounts.subprocess, 'run', side_effect=login):
            return self.manager.enroll('b')

    def ready(self):
        self.current()
        self.enroll_b()

    def test_api_enrollment_never_selects_or_restarts_a_subscription(self):
        self.current()
        before = {p: p.read_bytes() for p in (self.manager.auth, self.manager.config, self.manager.active)}
        self.idle.reset_mock()
        result = self.manager.enroll_api('api', api_key='sk-loopx-relay-synthetic-local-token', base_url='http://127.0.0.1:32100/v1')
        self.assertFalse(result['activated'])
        self.idle.assert_not_called()
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(self.manager.status()['slot_kinds'], {'a': 'subscription', 'api': 'api'})
        self.assertNotIn('sk-loopx-relay-synthetic-local-token', json.dumps(self.manager.status()))

    def test_api_slot_rejects_an_upstream_project_key(self):
        self.current()
        with self.assertRaises(ValueError):
            self.manager.enroll_api('api', api_key='sk-proj-' + 'synthetic-project-key' * 2,
                                    base_url='http://127.0.0.1:32100/v1')
        self.assertFalse(self.manager.slot('api').exists())

    def test_api_switch_and_subscription_return_reuse_transaction_recovery(self):
        self.current()
        original = self.manager.config.read_bytes()
        self.manager.enroll_api('api', api_key='sk-loopx-relay-synthetic-local-token', base_url='http://127.0.0.1:32100/v1')
        self.manager.switch('api')
        cfg = tomllib.loads(self.manager.config.read_text())
        self.assertEqual(cfg.get('model_provider', 'openai'), 'openai')
        self.assertEqual(cfg['forced_login_method'], 'api')
        self.assertEqual(cfg['openai_base_url'], 'http://127.0.0.1:32100/v1')
        self.assertEqual(self.manager.selected()[0]['slot'], 'api')
        self.manager.recover(rollback=True)
        self.assertEqual(self.manager.auth.read_bytes(), self.a)
        self.assertEqual(self.manager.config.read_bytes(), original)
        self.manager.switch('api')
        self.manager.switch('a')
        cfg = tomllib.loads(self.manager.config.read_text())
        self.assertEqual(cfg.get('model_provider', 'openai'), 'openai')
        self.assertEqual(cfg['forced_login_method'], 'chatgpt')
        self.assertEqual(self.manager.auth.read_bytes(), self.a)

    def test_api_fallback_refuses_non_loopback_and_changed_owned_provider(self):
        self.current()
        for endpoint in ['https://outside.example/v1', 'http://localhost:32100/v1',
                         'http://127.0.0.1:80/v1', 'http://127.0.0.1:32100/v1?forward=other']:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                self.manager.enroll_api('api', api_key='sk-loopx-relay-synthetic-local-token', base_url=endpoint)
        self.manager.enroll_api('api', api_key='sk-loopx-relay-synthetic-local-token', base_url='http://127.0.0.1:32100/v1')
        self.manager.switch('api')
        changed = self.manager.config.read_text().replace('http://127.0.0.1:32100/v1', 'https://outside.example/v1')
        self.manager.config.write_text(changed)
        with self.assertRaises(ValueError):
            self.manager.switch('a')
        self.assertEqual(self.manager.config.read_text(), changed)

    def test_readonly_status_creates_nothing_and_prints_no_identity(self):
        result = self.manager.status()
        self.assertFalse(self.manager.root.exists())
        self.assertEqual(result['slots'], [])
        self.assertFalse(result['live_authentication_verified'])
        self.assertNotIn('synthetic-access', json.dumps(result))
        self.assertNotIn('synthetic-a', json.dumps(result))

    def test_current_registration_preserves_auth_model_and_comments(self):
        self.manager.config.write_text('# kept\nmodel="gpt-example"\nmodel_provider="openai"\n[mcp_servers.demo]\ncommand="demo"\n')
        result = self.current()
        self.assertTrue(result['activated'])
        self.assertEqual(self.manager.auth.read_bytes(), self.a)
        self.assertIn('# kept\nmodel="gpt-example"', self.manager.config.read_text())
        self.assertEqual(self.manager.status()['active_slot'], 'a')
        for path in self.manager.root.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_native_enrollment_does_not_select_or_change_current_login(self):
        self.current()
        result = self.enroll_b()
        self.assertFalse(result['activated'])
        self.assertEqual(self.manager.auth.read_bytes(), self.a)
        self.assertEqual(self.manager.status()['active_slot'], 'a')
        self.assertEqual(list((self.manager.root / 'login-staging').iterdir()), [])

    def test_unused_provider_survives_account_enrollment_switch_and_rollback(self):
        tail = ('# Keep optional transport and unrelated sections verbatim.\n'
                '[model_providers.optional_http]\nname="Optional HTTP"\n'
                'wire_api="responses"\nrequires_openai_auth=true\n'
                'supports_websockets=false\n'
                '[mcp_servers.demo]\ncommand="demo"\n')
        for default in ('', 'model_provider="openai"\n'):
            with self.subTest(default=default):
                raw = ('# kept\nmodel="gpt-example"\n' + default + tail).encode()
                updated = accounts.file_config(raw)
                self.assertTrue(updated.endswith(tail.encode()))
                self.assertIn(('model="gpt-example"\n' + default).encode(), updated)
        self.manager.config.write_bytes(raw)
        self.ready()
        before = self.manager.config.read_bytes()
        self.manager.switch('b')
        self.assertEqual(self.manager.auth.read_bytes(), self.b)
        self.assertEqual(self.manager.config.read_bytes(), before)
        self.manager.recover(rollback=True)
        self.assertEqual(self.manager.auth.read_bytes(), self.a)
        self.assertEqual(self.manager.config.read_bytes(), before)
        self.assertTrue(before.endswith(tail.encode()))

    def test_gateway_provider_and_catalog_survive_api_and_subscription_switches(self):
        gateway = ('# kept\nmodel="gateway-model"\nmodel_provider="gateway"\n'
                   'model_catalog_json="models.json"\n'
                   '[model_providers.gateway]\nname="Gateway"\n'
                   'base_url="https://gateway.example/v1"\n'
                   'wire_api="responses"\n'
                   'requires_openai_auth=true\n'
                   '[mcp_servers.demo]\ncommand="demo"\n')
        self.manager.config.write_text(gateway)
        self.current()
        self.manager.enroll_api('api', api_key='sk-loopx-relay-synthetic-local-token',
                                base_url='http://127.0.0.1:32100/v1')
        self.manager.switch('api')
        cfg = tomllib.loads(self.manager.config.read_text())
        self.assertEqual(cfg['model_provider'], 'gateway')
        self.assertEqual(cfg['model_catalog_json'], 'models.json')
        self.assertEqual(cfg['forced_login_method'], 'api')
        self.assertNotIn('openai_base_url', cfg)
        self.manager.switch('a')
        cfg = tomllib.loads(self.manager.config.read_text())
        self.assertEqual(cfg['model_provider'], 'gateway')
        self.assertEqual(cfg['model_catalog_json'], 'models.json')
        self.assertEqual(cfg['forced_login_method'], 'chatgpt')
        self.assertNotIn('openai_base_url', cfg)
        self.assertEqual(cfg['mcp_servers']['demo']['command'], 'demo')

    def test_ambiguous_routes_and_profiles_are_not_converted(self):
        for raw in (b'model_provider="optional_http"\n',
                    b'[model_providers.openai]\n',
                    b'profile="custom"\n',
                    b'[profiles.custom]\nmodel_provider="optional_http"\n'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    accounts.file_config(raw)

    def test_live_enrollment_shares_cli_lease_and_preserves_switch_recovery(self):
        self.current()
        before = {p: p.read_bytes() for p in (self.manager.auth, self.manager.config,
                  self.manager.active, self.manager.root / 'last.json')}
        self.idle.reset_mock()
        self.idle.side_effect = ValueError('App is active')
        with accounts.credential_lock(self.home, shared=True):
            result = self.enroll_b()
        self.assertFalse(result['activated'])
        self.idle.assert_not_called()
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertFalse(self.manager.pending.exists())
        self.assertEqual(self.manager.slot('b').read_bytes(), self.b)

    def test_live_enrollment_without_current_slot_does_not_change_active_files(self):
        before = {p: p.read_bytes() for p in (self.manager.auth, self.manager.config)}
        self.idle.side_effect = ValueError('App is active')
        self.enroll_b()
        self.assertFalse(self.manager.active.exists())
        self.assertFalse(self.manager.pending.exists())
        self.assertFalse((self.manager.root / 'last.json').exists())
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(self.manager.status()['slots'], ['b'])

    def test_switch_preserves_real_sqlite_rollout_and_one_route(self):
        self.ready()
        db = self.spec['home'] / 'state_5.sqlite'
        with sqlite3.connect(db) as conn:
            conn.execute('create table example (title text)')
            conn.execute('insert into example values (?)', ('synthetic history',))
        conn.close()
        rollout = self.spec['home'] / 'sessions/example.jsonl'
        rollout.parent.mkdir()
        rollout.write_text('synthetic immutable history\n')
        before = {p: p.read_bytes() for p in (db, rollout)}
        result = self.manager.switch('b')
        self.assertEqual(result['home'], str(self.spec['home']))
        self.assertEqual(result['frontend'], str(self.spec['frontend']))
        self.assertEqual(self.manager.auth.read_bytes(), self.b)
        self.assertEqual(self.manager.status()['active_slot'], 'b')
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(list(self.home.glob('.codex-gpt-account-*')), [])

    def test_rotated_credentials_saved_before_leaving_and_reused_on_return(self):
        self.ready()
        rotated = fake_login('a', 'rotated')
        accounts.atomic(self.manager.auth, rotated)
        self.manager.switch('b')
        self.assertEqual(self.manager.slot('a').read_bytes(), rotated)
        self.manager.switch('a')
        self.assertEqual(self.manager.auth.read_bytes(), rotated)

    def test_selecting_current_slot_never_restores_older_refresh_token(self):
        self.current()
        rotated = fake_login('a', 'newer')
        accounts.atomic(self.manager.auth, rotated)
        self.manager.switch('a')
        self.assertEqual(self.manager.auth.read_bytes(), rotated)
        self.assertEqual(self.manager.slot('a').read_bytes(), rotated)

    def test_dry_run_changes_no_cache_journal_or_history(self):
        self.ready()
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        result = self.manager.switch('b', dry_run=True)
        self.assertTrue(result['preview'])
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_unknown_slot_and_untracked_current_login_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.switch('missing')
        self.enroll_b()
        with self.assertRaisesRegex(ValueError, 'current GPT login first'):
            self.manager.switch('b')
        self.assertEqual(self.manager.auth.read_bytes(), self.a)

    def test_blank_home_can_select_first_official_enrollment(self):
        self.manager.auth.unlink()
        self.enroll_b()
        self.manager.switch('b')
        self.assertEqual(self.manager.auth.read_bytes(), self.b)

    def test_duplicate_account_and_slot_never_overwritten(self):
        self.current()
        with self.assertRaises(ValueError):
            self.manager.enroll('a', current=True)
        with self.assertRaisesRegex(ValueError, 'already registered'):
            self.manager.enroll('alias', current=True)
        self.assertFalse(self.manager.slot('alias').exists())

    def test_external_login_change_cannot_overwrite_saved_slot(self):
        self.ready()
        accounts.atomic(self.manager.auth, fake_login('different'))
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            self.manager.switch('b')
        self.assertEqual(self.manager.slot('a').read_bytes(), self.a)
        with self.assertRaises(ValueError):
            apps.account_preflight(self.home, self.spec)

    def test_active_owner_refusal_has_no_vault_side_effects(self):
        self.idle.side_effect = ValueError('synthetic running process')
        with self.assertRaises(ValueError):
            self.current()
        self.assertFalse(self.manager.root.exists())

    def test_failed_login_preserves_current_route(self):
        with patch.object(accounts.subprocess, 'run', return_value=subprocess.CompletedProcess(['codex'], 1)):
            with self.assertRaisesRegex(ValueError, 'did not complete'):
                self.manager.enroll('b')
        self.assertEqual(self.manager.auth.read_bytes(), self.a)
        self.assertFalse(self.manager.slot('b').exists())

    def test_failed_write_rolls_back_without_unsettled_journal(self):
        self.ready()
        original = accounts.atomic
        failed = False
        def failing(path, raw):
            nonlocal failed
            if path == self.manager.auth and raw == self.b and not failed:
                failed = True
                raise OSError('synthetic storage failure')
            return original(path, raw)
        with patch.object(accounts, 'atomic', side_effect=failing):
            with self.assertRaises(OSError):
                self.manager.switch('b')
        self.assertEqual(self.manager.auth.read_bytes(), self.a)
        self.assertEqual(self.manager.status()['active_slot'], 'a')
        self.assertFalse(self.manager.pending.exists())

    def test_completed_operation_rollback_and_interrupted_recovery(self):
        self.ready()
        self.manager.switch('b')
        self.manager.recover(rollback=True)
        self.assertEqual(self.manager.auth.read_bytes(), self.a)
        self.manager.switch('b')
        journal = (self.manager.root / 'last.json').read_bytes()
        accounts.atomic(self.manager.pending, journal)  # Simulate a crash before journal removal.
        with self.assertRaisesRegex(ValueError, 'Interrupted'):
            apps.account_preflight(self.home, self.spec)
        self.manager.recover()
        self.assertEqual(self.manager.auth.read_bytes(), self.a)
        self.assertFalse(self.manager.pending.exists())

    def test_rollback_cannot_overwrite_later_user_changes(self):
        self.ready()
        self.manager.switch('b')
        self.manager.config.write_text(self.manager.config.read_text() + '# personal edit\n')
        with self.assertRaisesRegex(ValueError, 'Recovery rejected'):
            self.manager.recover(rollback=True)
        self.assertEqual(self.manager.auth.read_bytes(), self.b)

    def test_tampered_backup_and_journal_escape_rejected_before_restore(self):
        self.ready()
        result = self.manager.switch('b')
        journal = json.loads((self.manager.root / 'last.json').read_text())
        journal['files'][-1]['key'] = 'slot:../../escape'
        accounts.atomic(self.manager.pending, accounts.encoded(journal))
        with self.assertRaises(ValueError):
            self.manager.recover()
        self.assertEqual(self.manager.auth.read_bytes(), self.b)
        journal = json.loads((self.manager.root / 'last.json').read_text())
        accounts.atomic(self.manager.pending, accounts.encoded(journal))
        accounts.atomic(Path(result['backup']) / '0', b'synthetic-tamper')
        with self.assertRaises(ValueError):
            self.manager.recover()
        self.assertEqual(self.manager.auth.read_bytes(), self.b)

    def test_slot_symlink_and_public_cache_permissions_rejected(self):
        self.ready()
        path = self.manager.slot('b')
        path.unlink()
        path.symlink_to(self.manager.auth)
        with self.assertRaises(ValueError):
            self.manager.switch('b')
        path.unlink()
        self.manager.auth.chmod(0o644)
        with self.assertRaises(ValueError):
            self.manager.selected()
        for bad in ('../a', 'A', 'a/b', ''):
            with self.assertRaises(ValueError):
                self.manager.slot(bad)

    def test_vault_route_change_rejected_even_before_first_activation(self):
        self.enroll_b()
        changed = dict(self.spec, frontend=self.home / 'different frontend')
        with self.assertRaisesRegex(ValueError, 'different GPT route'):
            accounts.Accounts(self.home, changed, self.idle, None, None)

    def test_api_key_invalid_cache_and_explicit_keyring_are_not_converted(self):
        for raw in (b'{"OPENAI_API_KEY":"synthetic-secret"}', b'{broken-synthetic-secret', b'[]'):
            with self.assertRaises(ValueError) as error:
                accounts.identity(raw)
            self.assertNotIn('synthetic-secret', str(error.exception))
        for store in ('keyring', 'ephemeral'):
            with self.assertRaises(ValueError):
                accounts.file_config(f'cli_auth_credentials_store="{store}"\n'.encode())

    def test_optional_null_workspace_id_has_stable_identity(self):
        value = json.loads(self.a)
        value['tokens']['account_id'] = None
        null_identity = accounts.identity(accounts.encoded(value))
        del value['tokens']['account_id']
        self.assertEqual(accounts.identity(accounts.encoded(value)), null_identity)

    def test_real_open_sqlite_owner_is_detected_without_reading_it(self):
        if sys.platform != 'darwin':
            self.skipTest('macOS lsof ownership guard')
        conn = sqlite3.connect(self.spec['home'] / 'state_5.sqlite')
        self.addCleanup(conn.close)
        conn.execute('create table example (n int)')
        with patch.object(apps, 'app_bundle'), patch.object(apps, 'executable'), patch.object(apps, 'running', return_value=None):
            with self.assertRaisesRegex(ValueError, 'App/CLI process'):
                apps.account_idle(self.home, self.spec)

    def test_cooperating_cli_lease_blocks_switch_but_not_other_cli(self):
        self.ready()
        with accounts.credential_lock(self.home, shared=True):
            with accounts.credential_lock(self.home, shared=True):
                with self.assertRaises(BlockingIOError):
                    self.manager.switch('b')
        self.assertEqual(self.manager.auth.read_bytes(), self.a)



    def test_app_cli_backend_ownership_checks(self):
        with patch.object(apps, 'app_bundle'), patch.object(apps, 'executable'), patch.object(apps, 'running', return_value=42):
            with self.assertRaisesRegex(ValueError, 'Quit the GPT'):
                apps.account_idle(self.home, self.spec)
        with patch.object(apps, 'app_bundle'), patch.object(apps, 'executable'), patch.object(apps, 'running', return_value=None), patch.object(apps.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='p77\ncbackend\nf10\nau\n')):
            with self.assertRaisesRegex(ValueError, 'App/CLI process'):
                apps.account_idle(self.home, self.spec)

    def test_explicit_read_only_service_exception_does_not_exempt_writers(self):
        for output, permitted in [
            ('p77\ncfilesystem\nf10\nar\n', True),
            ('p77\ncfilesystem\nf10\nau\n', False),
            ('p77\ncfilesystem\nf10\nar\nf11\naw\n', False),
            ('p77\ncfilesystem\nf10\n', False),
            ('p77\ncfilesystem\n', False),
            ('p77\ncfilesystem\nfcwd\na \n', False),
            ('p88\ncbackend\nf10\nau\n', False),
            ('p77\ncfilesystem\nf10\nar\np88\ncbackend\nf20\nau\n', False),
        ]:
            with self.subTest(output=output), patch.object(apps, 'app_bundle'), patch.object(apps, 'executable'), patch.object(apps, 'running', return_value=None), patch.object(apps.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=output)):
                if permitted:
                    apps.account_idle(self.home, self.spec, [77])
                    with self.assertRaises(ValueError):
                        apps.account_idle(self.home, self.spec)
                else:
                    with self.assertRaises(ValueError):
                        apps.account_idle(self.home, self.spec, [77])



if __name__ == '__main__':
    unittest.main()
