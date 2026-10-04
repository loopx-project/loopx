"""Project owner-supplied model caches and verify isolated Codex App readback."""

from __future__ import annotations

import json
import os
import selectors
import stat
import subprocess
import tempfile
import time
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any

from .selectors import FAST_MODELS, MODEL_FAMILIES, ROUTES, SLOTS, VISIBLE_SELECTORS

ROUTE_SELECTORS = {slug: route["display_name"] for (slug, route) in ROUTES.items()}
ROUTE_SELECTORS.update(
    {model: f"{label} · Auto (legacy id)" for model, label in MODEL_FAMILIES.items()}
)
ARK_FLASH_ALIAS = "ark/deepseek-v4-flash"
ARK_LEGACY_FLASH_ALIAS = "deepseek-v4-flash"
ARK_FLASH_LABEL = "Ark · DeepSeek V4.1 Flash"
ARK_PRO_LABEL = "Ark · DeepSeek V4 Pro"
# The ids a stock operator configuration names. They are only the default view
# of the Ark rows: an operator who configures a newer upstream generation --
# DeepSeek-V4.1-Flash under its own endpoint id -- gets that id's row instead,
# because the configured id is the model a request has to name.
SHIPPED_ARK_MODEL = "deepseek-v4-flash-ga-260731"
SHIPPED_ARK_PRO_MODEL = "deepseek-v4-pro-ga-260813"


def release_suffix(model: str) -> str:
    """Return the release an upstream id pins, so two generations read apart."""

    tail = model.rsplit("-", 1)[-1]
    return tail if len(tail) == 6 and tail.isdigit() else model


def ark_selectors(flash_model: str, pro_model: str) -> dict[str, str]:
    """Return the Ark-backed catalog rows for the configured upstream ids.

    The two ids come from the operator's own configuration, so moving to a
    newer upstream generation is a configuration change rather than a source
    change. The two historical aliases stay, so a selector pinned to either
    spelling keeps resolving to whatever the endpoint now serves.
    """

    return {
        ARK_FLASH_ALIAS: ARK_FLASH_LABEL,
        ARK_LEGACY_FLASH_ALIAS: f"{ARK_FLASH_LABEL} (legacy id)",
        flash_model: f"{ARK_FLASH_LABEL} ({release_suffix(flash_model)})",
        pro_model: f"{ARK_PRO_LABEL} ({release_suffix(pro_model)})",
    }


# The rows a stock configuration publishes. Kept as the module-level view for
# readers that predate the configurable Ark ids; one operator's projection is
# the AppCatalog instance's own, built from the ids that operator configured.
SELECTORS = {
    **ROUTE_SELECTORS,
    **ark_selectors(SHIPPED_ARK_MODEL, SHIPPED_ARK_PRO_MODEL),
}
FAST_SELECTORS = set(FAST_MODELS)
ROUTE_FALLBACK_TAILS = {slug: route["tail"] for (slug, route) in ROUTES.items()}
EXPECTED_ROUTE_ORDERS = {
    slug: [f"codex-{slot}" for slot in route["order"]] + route["tail"]
    for (slug, route) in ROUTES.items()
}
AUTO_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=448)
    path.parent.chmod(448)
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


def source_model(path: Path, slug: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for model in payload.get("models", []):
        if isinstance(model, dict) and model.get("slug") == slug:
            return deepcopy(model)
    raise RuntimeError(f"model {slug} is absent from {path}")


def make_entry(
    source: dict[str, Any], slug: str, display_name: str, priority: int
) -> dict[str, Any]:
    entry = deepcopy(source)
    entry["slug"] = slug
    entry["display_name"] = display_name
    entry["description"] = display_name
    entry["priority"] = priority
    entry["visibility"] = "list"
    entry["upgrade"] = None
    return entry


def make_fast_entry(
    source: dict[str, Any], slug: str, display_name: str, priority: int
) -> dict[str, Any]:
    entry = make_entry(source, slug, display_name, priority)
    entry["default_service_tier"] = "fast"
    return entry


def read_messages(
    process: subprocess.Popen[str], wanted_id: int, timeout: float
) -> dict[str, Any]:
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(
                f"app-server exited before response {wanted_id}: {stderr[:300]}"
            )
        events = selector.select(timeout=min(0.2, deadline - time.monotonic()))
        if not events:
            continue
        line = process.stdout.readline()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("id") == wanted_id:
            return message
    raise TimeoutError(f"app-server response {wanted_id} timed out")


class AppCatalog:
    def __init__(self, runtime):
        self.runtime = runtime
        settings = runtime.settings
        self.CODEX_BINARY = settings.paths["codex_binary"]
        self.ASTRA_CACHE = settings.paths["astra_cache"]
        self.GPT_CACHE = settings.paths["gpt_cache"]
        self.ARK_CATALOG = settings.paths["ark_catalog"]
        self.ARK_PROFILE_CATALOG = settings.paths["ark_profile_catalog"]
        self.OUTPUT = runtime.MODEL_CATALOG
        self.AUTH_DIR = runtime.AUTH_DIR
        self.SLOTS_FILE = runtime.SLOTS_FILE
        self.PORT = runtime.PORT
        # The Ark rows follow the ids this operator configured, so a newer
        # upstream generation is adopted from the private configuration
        # instead of from a literal in this module.
        self.ARK_MODEL = runtime.ARK_MODEL
        self.ARK_PRO_MODEL = runtime.ARK_PRO_MODEL
        self.SELECTORS = {
            **ROUTE_SELECTORS,
            **ark_selectors(self.ARK_MODEL, self.ARK_PRO_MODEL),
        }

    def ark_source(self, slug: str) -> dict[str, Any]:
        for path in (self.ARK_CATALOG, self.ARK_PROFILE_CATALOG, self.OUTPUT):
            try:
                return source_model(path, slug)
            except (FileNotFoundError, RuntimeError):
                continue
        raise RuntimeError(f"missing cached Ark model: {slug}")

    def generate_catalog(self) -> dict[str, Any]:
        sources = {
            model: source_model(
                self.ASTRA_CACHE if model == "gpt-6-astra" else self.GPT_CACHE, model
            )
            for model in (*MODEL_FAMILIES, "gpt-5.6-luna")
        }
        entries = []
        for slug, label in self.SELECTORS.items():
            route = ROUTES.get(slug, ROUTES.get(f"auto/{slug}"))
            if route:
                source = deepcopy(sources[route["model"]])
                if slug.removeprefix("fast/").startswith(("auto/", "auto-with-ds/")):
                    source["default_reasoning_level"] = "high"
                    source["supported_reasoning_levels"] = [
                        level
                        for level in source["supported_reasoning_levels"]
                        if level["effort"] in AUTO_REASONING_EFFORTS
                    ]
            else:
                source = self.ark_source(
                    self.ARK_PRO_MODEL if slug == self.ARK_PRO_MODEL else self.ARK_MODEL
                )
            entry = make_entry(source, slug, label, len(entries) + 1)
            entry["visibility"] = "list" if slug in VISIBLE_SELECTORS else "hide"
            if route and route["tail"]:
                entry["additional_speed_tiers"] = []
                entry["service_tiers"] = []
            entry["default_service_tier"] = "fast" if slug in FAST_SELECTORS else None
            entries.append(entry)
        return {"models": entries}

    def cpa_models(self) -> set[str]:
        request = urllib.request.Request(f"http://127.0.0.1:{self.PORT}/v1/models")
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.load(response)
        data = payload.get("data", []) if isinstance(payload, dict) else []
        return {
            str(item["id"])
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }

    def route_traversal_readback(self) -> dict[str, Any]:
        slots = json.loads(self.SLOTS_FILE.read_text(encoding="utf-8"))
        priorities: dict[str, dict[str, int]] = {}
        for slot in SLOTS:
            auth_name = slots.get(slot)
            if not isinstance(auth_name, str):
                raise TypeError(f"missing symbolic OAuth slot {slot.upper()}")
            auth = json.loads((self.AUTH_DIR / auth_name).read_text(encoding="utf-8"))
            aliases = {
                item.get("alias"): item.get("routing-priority")
                for item in auth.get("model_aliases", [])
                if isinstance(item, dict)
                and isinstance(item.get("alias"), str)
                and isinstance(item.get("routing-priority"), int)
            }
            priorities[slot] = aliases
        rows: dict[str, Any] = {}
        for route, tail in ROUTE_FALLBACK_TAILS.items():
            members = sorted(
                ((f"codex-{slot}", priorities[slot].get(route)) for slot in SLOTS),
                key=lambda item: (-1 if item[1] is None else -item[1], item[0]),
            )
            if any(priority is None for (_, priority) in members):
                raise RuntimeError(f"missing route priority for {route}")
            ordered = [member for (member, _) in members] + list(tail)
            rows[route] = {
                "entrypoint": "affinity_then_first"
                if route.removeprefix("fast/").startswith(("auto/", "auto-with-ds/"))
                or route == "gpt-5.6-luna"
                else ordered[0],
                "ordered_candidates": ordered,
                "fallback_tail": list(tail),
                "max_cycles": 1,
            }
        return rows

    def write_catalog(self) -> None:
        payload = self.generate_catalog()
        private_write(
            self.OUTPUT, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )

    def app_config(self, home: Path) -> str:
        return "\n".join(
            [
                'model = "auto/gpt-5.6-sol"',
                'model_provider = "cpa"',
                'service_tier = "default"',
                f'model_catalog_json = "{self.OUTPUT}"',
                "",
                "[model_providers.cpa]",
                'name = "CPA · Auto"',
                f'base_url = "http://127.0.0.1:{self.PORT}/v1"',
                'wire_api = "responses"',
                "requires_openai_auth = false",
                "supports_websockets = false",
                "request_max_retries = 0",
                "stream_max_retries = 0",
                "",
                "[features]",
                "fast_mode = true",
                "",
                f'[projects."{home}"]',
                'trust_level = "trusted"',
                "",
            ]
        )

    def probe(self) -> dict[str, Any]:
        thread_responses: dict[str, dict[str, Any]] = {}
        with tempfile.TemporaryDirectory(prefix="cpa-codex-app-model-probe-") as raw:
            home = Path(raw)
            home.chmod(stat.S_IRWXU)
            private_write(home / "config.toml", self.app_config(home))
            env = os.environ.copy()
            env["CODEX_HOME"] = str(home)
            process = subprocess.Popen(
                [str(self.CODEX_BINARY), "app-server"],
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            assert process.stdin is not None
            try:
                initialize = {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {
                            "name": "cpa_model_probe",
                            "title": "CPA model probe",
                            "version": "0.1.0",
                        }
                    },
                }
                process.stdin.write(json.dumps(initialize) + "\n")
                process.stdin.flush()
                initialized = read_messages(process, 1, 10)
                if "error" in initialized:
                    raise RuntimeError("app-server initialize failed")
                process.stdin.write(
                    json.dumps({"method": "initialized", "params": {}}) + "\n"
                )
                process.stdin.write(
                    json.dumps(
                        {
                            "method": "model/list",
                            "id": 2,
                            "params": {"limit": 50, "includeHidden": True},
                        }
                    )
                    + "\n"
                )
                process.stdin.flush()
                response = read_messages(process, 2, 15)
                for request_id, model in (
                    (3, "auto/gpt-5.6-sol"),
                    (4, "fast/auto/gpt-5.6-sol"),
                    (5, "auto/gpt-6-astra"),
                    (6, "auto-with-ds/gpt-6-astra"),
                ):
                    process.stdin.write(
                        json.dumps(
                            {
                                "method": "thread/start",
                                "id": request_id,
                                "params": {
                                    "model": model,
                                    "modelProvider": "cpa",
                                    "cwd": str(home),
                                    "ephemeral": True,
                                },
                            }
                        )
                        + "\n"
                    )
                    process.stdin.flush()
                    thread_responses[model] = read_messages(process, request_id, 15)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        if "error" in response:
            raise RuntimeError("app-server model/list returned an error")
        data = response.get("result", {}).get("data", [])
        unexpected_visible = sorted(
            str(row.get("id"))
            for row in data
            if isinstance(row, dict)
            and not row.get("hidden")
            and row.get("id") not in self.SELECTORS
        )
        rows = {
            str(row.get("id")): {
                "displayName": row.get("displayName"),
                "hidden": row.get("hidden"),
                "inputModalities": row.get("inputModalities"),
                "defaultServiceTier": row.get("defaultServiceTier"),
                "additionalSpeedTiers": row.get("additionalSpeedTiers"),
                "serviceTiers": row.get("serviceTiers"),
                "supportedReasoningEfforts": [
                    effort.get("reasoningEffort")
                    for effort in row.get("supportedReasoningEfforts", [])
                    if isinstance(effort, dict)
                ],
            }
            for row in data
            if isinstance(row, dict) and row.get("id") in self.SELECTORS
        }
        missing = sorted(set(self.SELECTORS) - set(rows))
        hidden = sorted(
            key
            for key, row in rows.items()
            if bool(row.get("hidden")) != (key not in VISIBLE_SELECTORS)
        )
        wrong_display_names = sorted(
            key
            for (key, display_name) in self.SELECTORS.items()
            if rows.get(key, {}).get("displayName") != display_name
        )
        wrong_default_service_tiers = sorted(
            key
            for (key, row) in rows.items()
            if row.get("defaultServiceTier")
            != ("fast" if key in FAST_SELECTORS else None)
        )
        auto_efforts = rows.get("auto/gpt-5.6-sol", {}).get(
            "supportedReasoningEfforts", []
        )
        missing_auto_efforts = sorted(set(AUTO_REASONING_EFFORTS) - set(auto_efforts))
        live_models = self.cpa_models()
        cpa_missing = sorted(set(self.SELECTORS) - live_models)
        route_traversal = self.route_traversal_readback()
        route_mismatches = sorted(
            route
            for (route, expected) in EXPECTED_ROUTE_ORDERS.items()
            if route_traversal.get(route, {}).get("ordered_candidates") != expected
            or route_traversal.get(route, {}).get("max_cycles") != 1
        )
        thread_default_service_tiers = {
            model: payload.get("result", {}).get("serviceTier")
            if "error" not in payload
            else {"error": payload.get("error")}
            for (model, payload) in thread_responses.items()
        }
        fast_selector_plugin_active = self.runtime.fast_selector_plugin_ready(
            port=self.PORT
        )
        return {
            "schema_version": "cpa_codex_app_model_probe_v1",
            "codex_binary": str(self.CODEX_BINARY),
            "catalog_path": str(self.OUTPUT),
            "expected_selectors": sorted(self.SELECTORS),
            "projected_selectors": rows,
            "visible_selectors": sorted(
                key for key, row in rows.items() if not row.get("hidden")
            ),
            "route_traversal": route_traversal,
            "missing": missing,
            "unexpected_visible": unexpected_visible,
            "hidden": hidden,
            "wrong_display_names": wrong_display_names,
            "wrong_default_service_tiers": wrong_default_service_tiers,
            "missing_auto_efforts": missing_auto_efforts,
            "cpa_missing": cpa_missing,
            "route_mismatches": route_mismatches,
            "thread_default_service_tiers": thread_default_service_tiers,
            "fast_selector_plugin_active": fast_selector_plugin_active,
            "passed": not any(
                (
                    missing,
                    unexpected_visible,
                    hidden,
                    wrong_display_names,
                    wrong_default_service_tiers,
                    missing_auto_efforts,
                    cpa_missing,
                    route_mismatches,
                    not fast_selector_plugin_active,
                )
            ),
        }
