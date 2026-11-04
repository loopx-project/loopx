"""Private OpenAI keys and content-free observations for the opt-in API route."""
from __future__ import annotations

import json
import math
from email.utils import parsedate_to_datetime
import re
import urllib.error
import urllib.request
import uuid
import shutil
import subprocess
from pathlib import Path

from .native import atomic, private_read
from .native_host import HostError

SCHEMA = "codex_openai_api_keys_v1"


def api_policy(value: dict):
    node = shutil.which("node")
    if not node:
        raise HostError("node_unavailable")
    result = subprocess.run([node, "--no-warnings", "--experimental-strip-types",
                             str(Path(__file__).with_name("openai_policy_bridge.ts"))],
                            input=json.dumps(value), capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise HostError("policy_unavailable")
    return json.loads(result.stdout)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise HostError("key_check_unavailable")


def check_models(api_key: str) -> str:
    """No prompts, billing probes, redirects, retries or raw response storage."""
    request = urllib.request.Request("https://api.openai.com/v1/models", headers={
        "Authorization": "Bearer " + api_key, "Accept": "application/json",
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            encoded = response.read(1048577)
        if len(encoded) > 1048576:
            return "unavailable"
        value = json.loads(encoded)
        return "models_readable" if isinstance(value, dict) and isinstance(value.get("data"), list) else "unavailable"
    except urllib.error.HTTPError as error:
        error.close()
        return {401: "authentication_failed", 403: "permission_denied", 429: "rate_limited"}.get(error.code, "unavailable")
    except (HostError, OSError, ValueError):
        return "unavailable"


class OpenAIKeys:
    def __init__(self, owner, *, checker=check_models):
        self.owner, self.checker = owner, checker
        self.path = owner.root / "openai-keys.json"

    def _load(self) -> dict:
        return private_read(self.path, schema=SCHEMA) if self.path.exists() else {
            "schema_version": SCHEMA, "revision": 0, "keys": [],
        }

    @staticmethod
    def _projection(state: dict) -> dict:
        return {"schema_version": SCHEMA, "available": True, "revision": state["revision"],
                "balance_available": False,
                "keys": [{"id": row["id"], "label": row["label"],
                          "masked_key": "••••" + row["api_key"][-4:],
                          "check_status": row.get("check_status", "not_checked"),
                          "checked_at": row.get("checked_at"),
                          "runtime": row.get("runtime")}
                         for row in state["keys"]]}

    def status(self) -> dict:
        # Default-off read neither creates state nor contacts a provider.
        return self._projection(self._load())

    def _revision(self, expected_revision: int) -> dict:
        state = self._load()
        if type(expected_revision) is not int or state["revision"] != expected_revision:
            raise HostError("configuration_changed")
        return state

    def add(self, *, expected_revision: int, label: str, api_key: str) -> dict:
        if (not isinstance(api_key, str) or not re.fullmatch(r"sk-[A-Za-z0-9_-]{20,509}", api_key)
                or api_key.startswith("sk-admin-")):
            raise HostError("invalid_api_key")
        if (not isinstance(label, str) or not 1 <= len(label.strip()) <= 80
                or any(ord(c) < 32 for c in label) or "sk-" in label):
            raise HostError("invalid_key_label")
        with self.owner._lock(name="openai-keys.lock"):
            state = self._revision(expected_revision)
            if any(row["api_key"] == api_key for row in state["keys"]):
                raise HostError("duplicate_api_key")
            if len(state["keys"]) >= 16:
                raise HostError("key_limit_reached")
            state["keys"].append({"id": uuid.uuid4().hex, "label": label.strip(), "api_key": api_key})
            state["revision"] += 1
            atomic(self.path, state)
            return self._projection(state)

    def _row(self, state: dict, key_id: str) -> dict:
        if not isinstance(key_id, str):
            raise HostError("key_not_found")
        row = next((row for row in state["keys"] if row["id"] == key_id), None)
        if row is None:
            raise HostError("key_not_found")
        return row

    def remove(self, *, expected_revision: int, key_id: str) -> dict:
        with self.owner._lock(name="openai-keys.lock"):
            state = self._revision(expected_revision)
            state["keys"].remove(self._row(state, key_id))
            state["revision"] += 1
            atomic(self.path, state)
            return self._projection(state)

    def check(self, *, expected_revision: int, key_id: str) -> dict:
        with self.owner._lock(name="openai-keys.lock"):
            state = self._revision(expected_revision)
            row = self._row(state, key_id)
            row.update(check_status=self.checker(row["api_key"]), checked_at=self.owner.clock())
            state["revision"] += 1
            atomic(self.path, state)
            return self._projection(state)

    def candidates(self, model: str) -> list[dict]:
        state = self._load()
        ids = api_policy({"operation": "candidates", "now": self.owner.clock(), "model": model,
                          "keys": [{"id": row["id"], **{k: row.get("runtime", {}).get(k)
                                   for k in ("blocked_until", "blocked_model")}} for row in state["keys"]]})
        return [row for row in state["keys"] if row["id"] in ids]

    def record(self, key_id: str, *, status: int, code: str | None, model: str, headers: dict) -> dict:
        try:
            retry = float(headers.get("retry-after", ""))
            retry = retry if math.isfinite(retry) else None
        except (TypeError, ValueError):
            try:
                retry = max(1, parsedate_to_datetime(headers.get("retry-after", "")).timestamp() - self.owner.clock())
            except (TypeError, ValueError, OverflowError):
                retry = None
        outcome = api_policy({"operation": "outcome", "now": self.owner.clock(), "status": status,
                              "code": code, "retry_after": retry, "model": model})
        with self.owner._lock(name="openai-keys.lock", wait_seconds=15):
            state = self._load()
            row = next((row for row in state["keys"] if row["id"] == key_id), None)
            if row is not None:
                runtime = {k: outcome[k] for k in ("state", "blocked_until", "blocked_model")}
                runtime.update(observed_at=self.owner.clock(), model=model)
                for field in ("requests", "tokens"):
                    raw = headers.get("x-ratelimit-remaining-" + field, "")
                    if isinstance(raw, str) and raw.isdigit() and len(raw) <= 15:
                        runtime["remaining_" + field] = int(raw)
                row["runtime"] = runtime
                # Runtime observations do not invalidate a configuration edit.
                atomic(self.path, state)
        return outcome
