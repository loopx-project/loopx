"""Explicit local CPA operator. Managed extension calls never import this module."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .operator_catalog import ark_labels
from .operator_observations import ObservationMixin
from .selectors import FAST_MODELS, MODEL_FAMILIES, ROUTES, SLOTS, aliases_for_slot

GPT_MODEL = "gpt-5.6-sol"
AUTO_MODEL = f"auto/{GPT_MODEL}"
PREFER_A_MODEL = f"codex-a/{GPT_MODEL}"
PREFER_B_MODEL = f"codex-b/{GPT_MODEL}"
LUNA_MODEL = "gpt-5.6-luna"
FAST_AUTO_MODEL = f"fast/{AUTO_MODEL}"
FAST_PREFER_A_MODEL = f"fast/{PREFER_A_MODEL}"
FAST_PREFER_B_MODEL = f"fast/{PREFER_B_MODEL}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=448)
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        path.chmod(384)
    finally:
        temp.unlink(missing_ok=True)


class CPAOperator(ObservationMixin):
    def __init__(self, settings):
        self.settings = settings
        for name, value in settings.runtime_attributes().items():
            setattr(self, name, value)

    def check_target_boundaries(self):
        for directory in (
            self.RUNTIME_ROOT,
            self.AUTH_DIR,
            self.STATE_DIR,
            self.LOG_DIR,
            self.RUNTIME_TMP_ROOT,
        ):
            if directory.is_symlink():
                raise ValueError("operator directories must not be symbolic links")
        targets = (
            self.MODEL_CATALOG,
            self.PID_FILE,
            self.SLOTS_FILE,
            self.MANAGEMENT_KEY_FILE,
            self.STATUS_SNAPSHOT_FILE,
            self.RUNTIME_CONFIG,
        )
        if any(path.is_symlink() for path in targets):
            raise ValueError("operator targets must not be symbolic links")
        for name in self.load_slots().values():
            if (self.AUTH_DIR / name).is_symlink():
                raise ValueError("credential target must not be a symbolic link")

    def check_artifacts(self) -> None:
        if not self.BINARY.is_file():
            raise RuntimeError(f"missing pinned CPA binary: {self.BINARY}")
        actual = sha256(self.BINARY)
        if actual != self.BINARY_SHA256:
            raise RuntimeError(f"pinned CPA checksum mismatch: {actual}")
        if not self.FAST_SELECTOR_PLUGIN.is_file():
            raise RuntimeError(
                f"missing Fast selector plugin: {self.FAST_SELECTOR_PLUGIN}"
            )
        plugin_sha256 = sha256(self.FAST_SELECTOR_PLUGIN)
        if plugin_sha256 != self.FAST_SELECTOR_PLUGIN_SHA256:
            raise RuntimeError(
                f"Fast selector plugin checksum mismatch: {plugin_sha256}"
            )

    def prepare(self) -> None:
        self.check_artifacts()
        for directory in (
            self.RUNTIME_ROOT,
            self.AUTH_DIR,
            self.STATE_DIR,
            self.LOG_DIR,
            self.RUNTIME_TMP_ROOT,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=448)
            directory.chmod(448)

    def load_ark_key(self, env_file: Path | None) -> str:
        value = os.environ.get("ARK_API_KEY", "").strip()
        if value:
            return value
        if env_file is None:
            raise RuntimeError("ARK_API_KEY is unavailable to this command process")
        if not env_file.is_file():
            raise RuntimeError(f"Ark env file does not exist: {env_file}")
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            (key, candidate) = line.split("=", 1)
            normalized_key = key.strip()
            if normalized_key.startswith("export "):
                normalized_key = normalized_key.removeprefix("export ").strip()
            if normalized_key != "ARK_API_KEY":
                continue
            candidate = candidate.strip()
            if (
                len(candidate) >= 2
                and candidate[0] == candidate[-1]
                and (candidate[0] in {'"', "'"})
            ):
                candidate = candidate[1:-1]
            if candidate:
                return candidate
        raise RuntimeError("ARK_API_KEY is missing from the supplied env file")

    def management_key(self) -> str:
        if self.MANAGEMENT_KEY_FILE.is_file():
            key = self.MANAGEMENT_KEY_FILE.read_text(encoding="utf-8").strip()
            if len(key) < 32:
                raise RuntimeError("private management key is unexpectedly short")
            return key
        key = secrets.token_urlsafe(48)
        write_private(self.MANAGEMENT_KEY_FILE, key + "\n")
        return key

    def ark_fallback_models(self) -> list[str]:
        lines = []
        for slug, route in ROUTES.items():
            if not route["tail"]:
                continue
            lines.extend(
                [
                    f"      - name: {yaml_quote(self.ARK_MODEL)}",
                    f"        alias: {yaml_quote(slug)}",
                    f"        display-name: {yaml_quote(route['display_name'])}",
                    "        force-mapping: true",
                    "        is-compat: true",
                    "        input-modalities: [text]",
                    "        output-modalities: [text]",
                    "        thinking:",
                    '          levels: ["high", "medium", "low"]',
                ]
            )
        return lines

    def runtime_config(
        self,
        ark_key: str,
        port: int | None = None,
        management_secret: str | None = None,
    ) -> str:
        if port is None:
            port = self.PORT
        if management_secret is None:
            management_secret = self.management_key()
        # The same rows the App catalog publishes, labelled by the generation
        # the operator's own configured id proves, so one row cannot read as two
        # generations depending on which surface rendered it.
        labels = ark_labels(self.ARK_MODEL, self.ARK_PRO_MODEL)
        return "\n".join(
            [
                'host: "127.0.0.1"',
                f"port: {port}",
                f"auth-dir: {yaml_quote(str(self.AUTH_DIR))}",
                "debug: false",
                "logging-to-file: false",
                "usage-statistics-enabled: false",
                "remote-management:",
                "  allow-remote: false",
                f"  secret-key: {yaml_quote(management_secret)}",
                "  disable-control-panel: true",
                "request-retry: 10",
                "max-retry-credentials: 0",
                "max-retry-interval: 65",
                "force-model-prefix: false",
                "routing:",
                '  strategy: "fill-first"',
                "  session-affinity: true",
                '  session-affinity-ttl: "1h"',
                "codex:",
                "  stream-bootstrap-buffering: true",
                "  optimize-multi-agent-v2: true",
                "plugins:",
                "  enabled: true",
                f"  dir: {yaml_quote(str(self.PLUGIN_DIR))}",
                "  configs:",
                "    fast-selector-tier:",
                "      enabled: true",
                "      priority: 100",
                "openai-compatibility:",
                '  - name: "volcengine-ark-deepseek-v4-flash"',
                "    priority: 100",
                f"    base-url: {yaml_quote(self.ARK_BASE_URL)}",
                "    disable-cooling: false",
                "    request-retry: 1",
                "    api-key-entries:",
                f"      - api-key: {yaml_quote(ark_key)}",
                "    models:",
                *self.ark_fallback_models(),
                f"      - name: {yaml_quote(self.ARK_MODEL)}",
                '        alias: "ark/deepseek-v4-flash"',
                f"        display-name: {yaml_quote(labels['flash'])}",
                "        force-mapping: true",
                "        is-compat: true",
                "        input-modalities: [text]",
                "        output-modalities: [text]",
                "        thinking:",
                '          levels: ["high", "medium", "low"]',
                f"      - name: {yaml_quote(self.ARK_MODEL)}",
                '        alias: "deepseek-v4-flash"',
                f"        display-name: {yaml_quote(labels['flash_legacy'])}",
                "        force-mapping: true",
                "        is-compat: true",
                "        input-modalities: [text]",
                "        output-modalities: [text]",
                "        thinking:",
                '          levels: ["high", "medium", "low"]',
                f"      - name: {yaml_quote(self.ARK_MODEL)}",
                f"        alias: {yaml_quote(self.ARK_MODEL)}",
                f"        display-name: {yaml_quote(labels['flash_endpoint'])}",
                "        force-mapping: true",
                "        is-compat: true",
                "        input-modalities: [text]",
                "        output-modalities: [text]",
                "        thinking:",
                '          levels: ["high", "medium", "low"]',
                f"      - name: {yaml_quote(self.ARK_PRO_MODEL)}",
                f"        alias: {yaml_quote(self.ARK_PRO_MODEL)}",
                f"        display-name: {yaml_quote(labels['pro'])}",
                "        force-mapping: true",
                "        is-compat: true",
                "        input-modalities: [text]",
                "        output-modalities: [text]",
                "        thinking:",
                '          levels: ["high", "medium", "low"]',
                "",
            ]
        )

    def read_pid(self) -> int | None:
        try:
            return int(self.PID_FILE.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            return None

    def pid_alive(self, pid: int | None) -> bool:
        if pid is None or pid <= 1:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def wait_port(
        self, pid: int, port: int | None = None, timeout: float = 15.0
    ) -> None:
        if port is None:
            port = self.PORT
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.pid_alive(pid):
                raise RuntimeError("CPA exited before binding its loopback port")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise TimeoutError(f"CPA did not bind 127.0.0.1:{port}")

    def start(self, env_file: Path | None) -> None:
        self.prepare()
        current = self.read_pid()
        if self.pid_alive(current):
            print(f"already-running pid={current} port={self.PORT}")
            return
        self.PID_FILE.unlink(missing_ok=True)
        ark_key = self.load_ark_key(env_file)
        config_path = self.RUNTIME_CONFIG
        write_private(config_path, self.runtime_config(ark_key))
        log_path = self.LOG_DIR / "cpa.log"
        log_handle = log_path.open("ab", buffering=0)
        try:
            process = subprocess.Popen(
                [str(self.BINARY), "-config", str(config_path)],
                cwd=self.RUNTIME_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log_handle.close()
        try:
            self.wait_port(process.pid)
            time.sleep(0.75)
            if not self.pid_alive(process.pid):
                raise RuntimeError("CPA exited during post-bind stabilization")
            write_private(self.PID_FILE, f"{process.pid}\n")
        except Exception:
            config_path.unlink(missing_ok=True)
            raise
        print(
            f"started pid={process.pid} port={self.PORT} commit={self.SOURCE_COMMIT[:8]} auth_slots={','.join(self.active_slots()) or 'none'}"
        )

    def serve(self, env_file: Path | None) -> None:
        """Run CPA in the foreground for launchd supervision."""
        self.prepare()
        ark_key = self.load_ark_key(env_file)
        write_private(self.RUNTIME_CONFIG, self.runtime_config(ark_key))
        write_private(self.PID_FILE, f"{os.getpid()}\n")
        os.chdir(self.RUNTIME_ROOT)
        os.execv(
            str(self.BINARY), [str(self.BINARY), "-config", str(self.RUNTIME_CONFIG)]
        )

    def launchd_loaded(self) -> bool:
        completed = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{self.LAUNCHD_LABEL}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return completed.returncode == 0

    def stop(self) -> None:
        if self.launchd_loaded():
            raise RuntimeError(
                "runtime is launchd-managed; boot out the dedicated LaunchAgent before stopping"
            )
        pid = self.read_pid()
        if not self.pid_alive(pid):
            self.PID_FILE.unlink(missing_ok=True)
            print("already-stopped")
            return
        assert pid is not None
        command = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        if str(self.BINARY) not in command:
            raise RuntimeError(f"refusing to signal unrelated pid {pid}")
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.pid_alive(pid):
            time.sleep(0.05)
        if self.pid_alive(pid):
            raise RuntimeError(f"CPA pid {pid} did not stop after SIGTERM")
        self.PID_FILE.unlink(missing_ok=True)
        self.RUNTIME_CONFIG.unlink(missing_ok=True)
        print(f"stopped pid={pid}")

    def load_slots(self) -> dict[str, str]:
        if not self.SLOTS_FILE.is_file():
            return {}
        data = json.loads(self.SLOTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("invalid OAuth slot state")
        if any(
            key not in SLOTS
            or not isinstance(value, str)
            or Path(value).name != value
            or not value.endswith(".json")
            for key, value in data.items()
        ):
            raise ValueError("invalid OAuth slot filename")
        if len(set(data.values())) != len(data):
            raise ValueError("OAuth slots must reference distinct credentials")
        return data

    def active_slots(self) -> list[str]:
        return sorted(
            slot
            for (slot, name) in self.load_slots().items()
            if (self.AUTH_DIR / name).is_file()
        )

    def patch_slot_auth(self, path: Path, slot: str) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or str(data.get("type", "")).lower() != "codex":
            raise RuntimeError("OAuth result is not a Codex auth record")
        data.update(
            {
                "priority": 400 - 100 * SLOTS.index(slot),
                "request_retry": 10,
                "disable_cooling": False,
                "websockets": False,
                "loopx_slot": slot,
                "model_aliases": aliases_for_slot(slot),
            }
        )
        write_private(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    def reconcile(self) -> None:
        """Validate the entire slot set before changing any routing metadata."""
        self.prepare()
        self.check_target_boundaries()
        slots = self.load_slots()
        missing = [
            slot
            for slot in SLOTS
            if slot not in slots or not (self.AUTH_DIR / slots[slot]).is_file()
        ]
        if missing:
            raise ValueError(
                "cannot reconcile missing OAuth slots: " + ",".join(missing)
            )
        identities = set()
        for slot in SLOTS:
            data = json.loads((self.AUTH_DIR / slots[slot]).read_text())
            if (
                data.get("type") != "codex"
                or not data.get("account_id")
                or data["account_id"] in identities
            ):
                raise ValueError("slots require distinct Codex account identities")
            identities.add(data["account_id"])
        for slot in SLOTS:
            self.patch_slot_auth(self.AUTH_DIR / slots[slot], slot)

    def fetch_models(self, port: int | None = None) -> list[str]:
        if port is None:
            port = self.PORT
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/models")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(
                f"CPA model endpoint unavailable: {type(error).__name__}"
            ) from error
        data = payload.get("data", []) if isinstance(payload, dict) else []
        return sorted(
            str(item["id"])
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )

    def fetch_management_auth_files(
        self, port: int | None = None
    ) -> list[dict[str, Any]]:
        if port is None:
            port = self.PORT
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v0/management/auth-files",
            headers={"Authorization": "Bearer " + self.management_key()},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(
                f"CPA management endpoint unavailable: {type(error).__name__}"
            ) from error
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            raise TypeError(
                "CPA management endpoint returned an invalid auth-file list"
            )
        return [item for item in files if isinstance(item, dict)]

    def reset_cooldown(self, slot: str) -> dict[str, Any]:
        """Permit a fresh attempt after an externally confirmed early quota recovery.

        This clears only CPA routing memory; it does not replenish upstream quota
        or declare the account healthy. The next request supplies fresh evidence.
        """
        if slot not in SLOTS:
            raise ValueError("unknown OAuth slot")
        name = self.load_slots().get(slot)
        matches = [
            entry
            for entry in self.fetch_management_auth_files()
            if name is not None and entry.get("name") == name
        ]
        if len(matches) != 1:
            raise RuntimeError("expected exactly one registered slot credential")
        entry = matches[0]
        if entry.get("disabled"):
            raise RuntimeError("refusing to recover an explicitly disabled slot")
        auth_index = entry.get("auth_index")
        if not isinstance(auth_index, str) or not auth_index.strip():
            raise RuntimeError("registered slot has no management auth index")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.PORT}/v0/management/reset-quota",
            data=json.dumps({"auth_index": auth_index}).encode(),
            headers={
                "Authorization": "Bearer " + self.management_key(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError("CPA cooldown reset failed") from error
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise RuntimeError("CPA cooldown reset was not acknowledged")
        return {
            "profile_id": f"codex-{slot}",
            "cooldown_cleared": True,
            "live_verification_required": True,
        }

    def fetch_management_plugins(self, port: int | None = None) -> list[dict[str, Any]]:
        if port is None:
            port = self.PORT
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v0/management/plugins",
            headers={"Authorization": "Bearer " + self.management_key()},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(
                f"CPA plugin endpoint unavailable: {type(error).__name__}"
            ) from error
        plugins = payload.get("plugins") if isinstance(payload, dict) else None
        if not isinstance(plugins, list):
            raise TypeError("CPA plugin endpoint returned an invalid plugin list")
        return [item for item in plugins if isinstance(item, dict)]

    def fast_selector_plugin_ready(self, port: int | None = None) -> bool:
        if port is None:
            port = self.PORT
        return any(
            item.get("id") == "fast-selector-tier"
            and item.get("registered") is True
            and (item.get("effective_enabled") is True)
            for item in self.fetch_management_plugins(port=port)
        )

    def route_status(self, *, modality: str, fast: bool) -> None:
        self.prepare()
        if modality not in {"text", "image"}:
            raise ValueError("route-status modality must be text or image")
        payload = {
            "schema_version": "cpa_route_status_v0",
            "credential_free": True,
            "host_identity": {
                "state": "retained",
                "projection": "not_projected",
                "route_binding": "none",
            },
            "execution_observation": self.latest_execution_observation(
                modality=modality, fast=fast
            ),
            "account_observations": self.account_observations(),
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for private_value in self.load_slots().values():
            if private_value and private_value in serialized:
                raise RuntimeError("private auth filename reached route status")
        write_private(self.STATUS_SNAPSHOT_FILE, serialized + "\n")
        print(serialized)

    def validate(self) -> None:
        self.check_target_boundaries()
        self.check_artifacts()
        slots = self.load_slots()
        errors: list[str] = []
        if not self.MODEL_CATALOG.is_file():
            errors.append("model-catalog-missing")
        else:
            catalog = json.loads(self.MODEL_CATALOG.read_text(encoding="utf-8"))
            by_slug = {
                item.get("slug"): item
                for item in catalog.get("models", [])
                if isinstance(item, dict) and isinstance(item.get("slug"), str)
            }
            expected_modalities = {
                **{slug: {"text", "image"} for slug in ROUTES},
                AUTO_MODEL: {"text", "image"},
                PREFER_A_MODEL: {"text", "image"},
                PREFER_B_MODEL: {"text", "image"},
                LUNA_MODEL: {"text", "image"},
                FAST_AUTO_MODEL: {"text", "image"},
                FAST_PREFER_A_MODEL: {"text", "image"},
                FAST_PREFER_B_MODEL: {"text", "image"},
                "ark/deepseek-v4-flash": {"text"},
                "deepseek-v4-flash": {"text"},
                self.ARK_MODEL: {"text"},
                self.ARK_PRO_MODEL: {"text"},
            }
            for model, expected in expected_modalities.items():
                item = by_slug.get(model)
                actual = set(item.get("input_modalities", [])) if item else set()
                if actual != expected:
                    errors.append(f"model-catalog-modalities:{model}")
            for model in ROUTES:
                item = by_slug.get(model) or {}
                speed_tiers = set(item.get("additional_speed_tiers", []))
                service_tiers = {
                    tier.get("id")
                    for tier in item.get("service_tiers", [])
                    if isinstance(tier, dict)
                }
                if ROUTES[model]["tail"]:
                    if speed_tiers or service_tiers:
                        errors.append(f"model-catalog-standard-only:{model}")
                elif "fast" not in speed_tiers or "priority" not in service_tiers:
                    errors.append(f"model-catalog-fast-tier:{model}")
                expected_default = "fast" if model in FAST_MODELS else None
                if item.get("default_service_tier") != expected_default:
                    errors.append(f"model-catalog-fast-default:{model}")
        for slot in SLOTS:
            name = slots.get(slot)
            if not name or not (self.AUTH_DIR / name).is_file():
                errors.append(f"slot-{slot}-missing")
                continue
            data = json.loads((self.AUTH_DIR / name).read_text(encoding="utf-8"))
            want_priority = 400 - 100 * SLOTS.index(slot)
            alias_entries = {
                item.get("alias"): item
                for item in data.get("model_aliases", [])
                if isinstance(item, dict) and isinstance(item.get("alias"), str)
            }
            if data.get("priority") != want_priority:
                errors.append(f"slot-{slot}-priority")
            expected_aliases = aliases_for_slot(slot)
            if any(entry["alias"] not in alias_entries for entry in expected_aliases):
                errors.append(f"slot-{slot}-aliases")
            expected_route_priorities = {
                entry["alias"]: entry["routing-priority"] for entry in expected_aliases
            }
            for entry in expected_aliases:
                if alias_entries.get(entry["alias"], {}).get("name") != entry["name"]:
                    errors.append(f"slot-{slot}-upstream-model:{entry['alias']}")
            for alias, expected_priority in expected_route_priorities.items():
                if (
                    alias_entries.get(alias, {}).get("routing-priority")
                    != expected_priority
                ):
                    errors.append(f"slot-{slot}-routing-priority:{alias}")
            if data.get("websockets") is not False:
                errors.append(f"slot-{slot}-websocket")
        if errors:
            raise RuntimeError("qualification incomplete: " + ",".join(errors))
        if self.pid_alive(self.read_pid()):
            if not self.fast_selector_plugin_ready():
                raise RuntimeError("runtime Fast selector plugin is not active")
            models = set(self.fetch_models())
            required = {
                *ROUTES,
                *MODEL_FAMILIES,
                AUTO_MODEL,
                GPT_MODEL,
                PREFER_A_MODEL,
                PREFER_B_MODEL,
                LUNA_MODEL,
                *FAST_MODELS,
                "ark/deepseek-v4-flash",
                *self.ARK_LEGACY_MODELS,
                self.ARK_PRO_MODEL,
            }
            missing = sorted(required - models)
            if missing:
                raise RuntimeError(
                    "runtime model catalog missing: " + ",".join(missing)
                )
        print(
            "validate-ok slots=A,B,C models=Sol,Astra auto=A-B-C auto-with-ds=A-B-C-Ark"
        )
