"""Offline regression tests for the opt-in local operator (no real credentials)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loopx_codex_provider_routing.operator import rollback, run, snapshot
from loopx_codex_provider_routing.operator_catalog import (
    SELECTORS,
    AppCatalog,
    ark_generation,
    release_suffix,
)
from loopx_codex_provider_routing.operator_runtime import (
    CPAOperator,
    sha256,
    write_private,
)
from loopx_codex_provider_routing.operator_settings import OperatorSettings
from loopx_codex_provider_routing.selectors import (
    ROUTES,
    VISIBLE_SELECTORS,
    aliases_for_slot,
    compiled_routes,
)


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cpa-operator-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        runtime = self.root / "runtime"
        paths = {key: str(self.root / key) for key in OperatorSettings.PATH_KEYS}
        paths.update(
            runtime_root=str(runtime), temporary_root=str(self.root / "temporary")
        )
        self.data = {
            "schema_version": "loopx_cpa_local_operator_v1",
            "paths": paths,
            "binary_sha256": "0" * 64,
            "plugin_sha256": "0" * 64,
            "source_commit": "0" * 40,
            "port": 19876,
            "launchd_label": "org.example.cpa-test",
            "ark_base_url": "https://api.example.invalid/v1",
            "ark_model": "deepseek-v4-flash-ga-260731",
            "ark_pro_model": "deepseek-v4-pro-ga-260813",
        }
        self.settings = OperatorSettings(self.data)
        self.runtime = CPAOperator(self.settings)
        self.config = self.root / "operator.json"
        write_private(self.config, json.dumps(self.data))

    def seed(self):
        r = self.runtime
        r.AUTH_DIR.mkdir(parents=True)
        slots = {}
        for slot in "abc":
            name = f"{slot}.json"
            write_private(
                r.AUTH_DIR / name,
                json.dumps(
                    {
                        "type": "codex",
                        "account_id": f"fixture-{slot}",
                        "access_token": "fixture-access",
                        "refresh_token": "fixture-refresh",
                    }
                ),
            )
            r.patch_slot_auth(r.AUTH_DIR / name, slot)
            slots[slot] = name
        write_private(r.SLOTS_FILE, json.dumps(slots))
        write_private(r.MODEL_CATALOG, json.dumps({"models": []}))

    def test_default_is_plan_without_files_or_processes(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(run(["--config", str(self.config), "serve"]), 0)
        self.assertFalse(self.runtime.RUNTIME_ROOT.exists())
        receipt = json.loads(output.getvalue())
        self.assertFalse(receipt["executed"])
        self.assertNotIn(str(self.root), output.getvalue())

    def test_private_configuration_and_exact_targets(self):
        self.config.chmod(0o644)
        with self.assertRaises(ValueError):
            OperatorSettings.read(self.config)
        self.data["paths"]["runtime_root"] = "/"
        with self.assertRaises(ValueError):
            OperatorSettings(self.data)

    def test_reject_state_inside_git_worktree(self):
        (self.root / ".git").mkdir()
        with self.assertRaises(ValueError):
            OperatorSettings(self.data)

    def test_reject_symlink_state(self):
        self.runtime.RUNTIME_ROOT.mkdir()
        other = self.root / "other"
        other.mkdir()
        self.runtime.AUTH_DIR.symlink_to(other)
        with self.assertRaises(ValueError):
            self.runtime.check_target_boundaries()

    def test_reject_slot_escape_and_duplicate_credentials(self):
        for slots in (
            {"a": "../outside.json"},
            {"a": "a.json", "b": "a.json"},
            {"d": "d.json"},
        ):
            write_private(self.runtime.SLOTS_FILE, json.dumps(slots))
            with self.assertRaises(ValueError):
                self.runtime.load_slots()

    def test_three_account_ring_and_model_parity(self):
        expected = {
            "auto": list("abc"),
            "codex-a": list("abc"),
            "codex-b": list("bca"),
            "codex-c": list("cab"),
        }
        for model in ("gpt-5.6-sol", "gpt-6-astra"):
            for prefix, order in expected.items():
                for fast in (False, True):
                    slug = ("fast/" if fast else "") + prefix + "/" + model
                    entries = {
                        slot: next(
                            e for e in aliases_for_slot(slot) if e["alias"] == slug
                        )
                        for slot in "abc"
                    }
                    actual = sorted(
                        entries, key=lambda slot: -entries[slot]["routing-priority"]
                    )
                    self.assertEqual(actual, order)
                    self.assertTrue(all(e["name"] == model for e in entries.values()))
                    self.assertEqual(ROUTES[slug]["tail"], [])
                    if prefix == "auto" and not fast:
                        self.assertTrue(all(e["fork"] for e in entries.values()))
        self.assertEqual(ROUTES["gpt-5.6-luna"]["tail"], [])
        compiled = compiled_routes()
        for row in compiled["selector_rows"]:
            expected_tail = (
                ["ark-text"] if row["slug"].startswith("auto-with-ds/") else []
            )
            self.assertEqual(
                [p for p in row["candidates"] if p == "ark-text"], expected_tail
            )

    def test_runtime_never_maps_subscription_selectors_to_ark(self):
        config = self.runtime.runtime_config("fixture-key", management_secret="fixture")
        compatibility = config.split("openai-compatibility:", 1)[1]
        self.assertIn('alias: "ark/deepseek-v4-flash"', compatibility)
        native_only = [slug for slug, route in ROUTES.items() if not route["tail"]]
        for alias in (*native_only, "gpt-5.6-sol", "gpt-6-astra"):
            self.assertNotIn(f'alias: "{alias}"', compatibility)
        self.assertIn("disable-cooling: false", config)

    def test_ark_rows_name_the_generation_the_configured_id_proves(self):
        """Old, new and custom upstream ids keep truthful generations.

        The Ark rows are the only DeepSeek surface that names an upstream
        endpoint id, so the row is adopted from the operator's configuration
        and labelled by the generation that id proves -- never by a literal
        here. Reusing one label for every flash id made a stock V4 endpoint read
        as V4.1 in both the App catalog and the CPA config.
        """

        cases = (
            # (configured id, flash row, legacy alias, endpoint row, pro row)
            (
                "deepseek-v4-flash-ga-260731",
                "Ark · DeepSeek V4 Flash",
                "Ark · DeepSeek V4 Flash (legacy id)",
                "Ark · DeepSeek V4 Flash (260731)",
                "Ark · DeepSeek V4 Pro (260813)",
            ),
            (
                "deepseek-v4-1-flash-260910",
                "Ark · DeepSeek V4.1 Flash",
                "Ark · DeepSeek V4.1 Flash (legacy id)",
                "Ark · DeepSeek V4.1 Flash (260910)",
                "Ark · DeepSeek V4 Pro (260813)",
            ),
            (
                "custom-ark-flash",
                "Ark · custom-ark-flash",
                "Ark · custom-ark-flash (legacy id)",
                "Ark · custom-ark-flash (endpoint id)",
                "Ark · custom-ark-pro (endpoint id)",
            ),
        )
        for flash_model, flash, legacy, endpoint, pro in cases:
            data = {**self.data, "ark_model": flash_model}
            if flash_model == "custom-ark-flash":
                data["ark_pro_model"] = "custom-ark-pro"
            runtime = CPAOperator(OperatorSettings(data))
            catalog = AppCatalog(runtime)
            self.assertEqual(catalog.SELECTORS["ark/deepseek-v4-flash"], flash)
            self.assertEqual(catalog.SELECTORS["deepseek-v4-flash"], legacy)
            self.assertEqual(catalog.SELECTORS[flash_model], endpoint)
            self.assertEqual(catalog.SELECTORS[data["ark_pro_model"]], pro)
            config = runtime.runtime_config("fixture-key", management_secret="fixture")
            # Both consumers render the same generation for the same id.
            for label in (flash, legacy, endpoint, pro):
                self.assertIn(f'display-name: "{label}"', config)

    def test_shipped_ark_defaults_stay_v4_and_the_canonical_default_is_v41(self):
        """The shipped rows and the product default keep distinct generations."""

        configured = AppCatalog(self.runtime).SELECTORS
        self.assertEqual(configured["ark/deepseek-v4-flash"], "Ark · DeepSeek V4 Flash")
        self.assertEqual(
            configured["deepseek-v4-flash-ga-260731"], "Ark · DeepSeek V4 Flash (260731)"
        )
        self.assertEqual(configured["ark/deepseek-v4-flash"], SELECTORS["ark/deepseek-v4-flash"])
        self.assertEqual(ark_generation("deepseek-flash"), "V4.1")
        self.assertEqual(ark_generation("deepseek-v4-1-flash-260910"), "V4.1")
        self.assertEqual(ark_generation("deepseek-v4-flash-ga-260731"), "V4")
        self.assertIsNone(ark_generation("custom-ark-flash"))
        self.assertEqual(release_suffix("deepseek-flash"), "deepseek-flash")

    def test_catalog_rows_use_the_same_label_as_the_runtime_config(self):
        self.seed()
        self.seed_model_caches()
        rows = {
            m["slug"]: m["display_name"]
            for m in AppCatalog(self.runtime).generate_catalog()["models"]
        }
        config = self.runtime.runtime_config("fixture-key", management_secret="fixture")
        self.assertEqual(rows["ark/deepseek-v4-flash"], "Ark · DeepSeek V4 Flash")
        self.assertEqual(
            rows[self.data["ark_model"]], "Ark · DeepSeek V4 Flash (260731)"
        )
        self.assertIn(f'display-name: "{rows["ark/deepseek-v4-flash"]}"', config)
        self.assertIn(f'display-name: "{rows[self.data["ark_model"]]}"', config)

    def test_cooldown_reset_is_scoped_and_not_a_health_claim(self):
        self.seed()
        entry = {"name": "b.json", "auth_index": "fixture-index", "disabled": False}
        with (
            patch.object(
                self.runtime, "fetch_management_auth_files", return_value=[entry]
            ),
            patch("urllib.request.urlopen") as urlopen,
        ):
            urlopen.return_value.__enter__.return_value = io.StringIO('{"status":"ok"}')
            result = self.runtime.reset_cooldown("b")
            request = urlopen.call_args.args[0]
            self.assertTrue(request.full_url.endswith("/v0/management/reset-quota"))
            self.assertEqual(json.loads(request.data), {"auth_index": "fixture-index"})
            self.assertEqual(result["profile_id"], "codex-b")
            self.assertTrue(result["live_verification_required"])
            self.assertNotIn("fixture-index", json.dumps(result))

    def test_cooldown_reset_rejects_disabled_missing_and_ambiguous_slots(self):
        self.seed()
        for entries in (
            [],
            [{"name": "b.json", "disabled": True, "auth_index": "fixture"}],
            [{"name": "b.json"}],
            [{"name": "b.json"}, {"name": "b.json"}],
        ):
            with (
                patch.object(
                    self.runtime, "fetch_management_auth_files", return_value=entries
                ),
                patch("urllib.request.urlopen") as urlopen,
            ):
                with self.assertRaises(RuntimeError):
                    self.runtime.reset_cooldown("b")
                urlopen.assert_not_called()

    def test_cooldown_reset_requires_slot_and_explicit_execution(self):
        with self.assertRaises(ValueError):
            run(["--config", str(self.config), "reset-cooldown"])
        with (
            patch.object(CPAOperator, "reset_cooldown") as reset,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                run(["--config", str(self.config), "reset-cooldown", "--slot", "b"]), 0
            )
            reset.assert_not_called()

    def test_reconcile_preserves_tokens_and_is_idempotent(self):
        self.seed()
        path = self.runtime.AUTH_DIR / "c.json"
        before = path.read_bytes()
        self.runtime.patch_slot_auth(path, "c")
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(json.loads(before)["refresh_token"], "fixture-refresh")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rollback_preserves_rotated_credentials(self):
        self.seed()
        backup = snapshot(self.runtime)
        path = self.runtime.AUTH_DIR / "a.json"
        data = json.loads(path.read_text())
        data.update(
            refresh_token="fixture-refreshed",
            access_token="fixture-new-access",
            priority=1,
        )
        write_private(path, json.dumps(data))
        rollback(self.runtime, backup)
        restored = json.loads(path.read_text())
        self.assertEqual(restored["priority"], 400)
        self.assertEqual(restored["refresh_token"], "fixture-refreshed")
        self.assertEqual(restored["access_token"], "fixture-new-access")

    def test_rollback_rejects_tampering_before_any_write(self):
        self.seed()
        backup = snapshot(self.runtime)
        before = self.runtime.SLOTS_FILE.read_bytes()
        directory = self.runtime.STATE_DIR / "operator-backups" / backup
        (directory / "0.json").write_text("{}")
        with self.assertRaises(ValueError):
            rollback(self.runtime, backup)
        self.assertEqual(before, self.runtime.SLOTS_FILE.read_bytes())
        with self.assertRaises(ValueError):
            rollback(self.runtime, "../outside")

    def test_catalog_uses_native_model_metadata_and_fast_parity(self):
        self.seed()
        self.seed_model_caches()
        catalog = AppCatalog(self.runtime)
        rows = {m["slug"]: m for m in catalog.generate_catalog()["models"]}
        self.assertEqual(len(rows), 25)
        self.assertEqual(
            {slug for slug, row in rows.items() if row["visibility"] == "list"},
            {
                "auto/gpt-5.6-sol",
                "fast/auto/gpt-5.6-sol",
                "auto-with-ds/gpt-5.6-sol",
                "auto/gpt-6-astra",
                "fast/auto/gpt-6-astra",
                "auto-with-ds/gpt-6-astra",
                "gpt-5.6-luna",
            },
        )
        self.assertEqual(
            rows["gpt-6-astra"]["context_window"],
            rows["auto/gpt-6-astra"]["context_window"],
        )
        for slug in VISIBLE_SELECTORS:
            if slug.startswith("auto-with-ds/"):
                self.assertEqual(rows[slug]["service_tiers"], [])
        for slug, row in rows.items():
            self.assertEqual(
                row["default_service_tier"],
                "fast" if slug.startswith("fast/") else None,
            )
            if "astra" in slug:
                self.assertEqual(row["context_window"], 100000)
        self.assertEqual(
            rows["codex-c/gpt-6-astra"]["supported_reasoning_levels"][-1]["effort"],
            "ultra",
        )
        self.assertNotIn(
            "ultra",
            [
                r["effort"]
                for r in rows["auto/gpt-6-astra"]["supported_reasoning_levels"]
            ],
        )
        catalog.write_catalog()
        first = sha256(self.runtime.MODEL_CATALOG)
        catalog.write_catalog()
        self.assertEqual(first, sha256(self.runtime.MODEL_CATALOG))
        with (
            patch.object(self.runtime, "check_artifacts"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.runtime.validate()
            broken = json.loads(self.runtime.MODEL_CATALOG.read_text())
            row = next(
                row
                for row in broken["models"]
                if row["slug"] == "auto-with-ds/gpt-6-astra"
            )
            row["additional_speed_tiers"] = ["fast"]
            write_private(self.runtime.MODEL_CATALOG, json.dumps(broken))
            with self.assertRaisesRegex(RuntimeError, "standard-only"):
                self.runtime.validate()

    def seed_model_caches(self, ark_models=None):
        """Write the private model caches a real operator config points at."""

        base = {
            "input_modalities": ["text", "image"],
            "supported_reasoning_levels": [
                {"effort": e}
                for e in ("low", "medium", "high", "xhigh", "max", "ultra")
            ],
            "additional_speed_tiers": ["fast"],
            "service_tiers": [{"id": "priority"}],
        }
        ark_models = ark_models or [
            self.data["ark_model"],
            self.data["ark_pro_model"],
        ]
        for key, models in (
            ("gpt_cache", ["gpt-5.6-sol", "gpt-5.6-luna"]),
            ("astra_cache", ["gpt-6-astra"]),
            ("ark_catalog", ark_models),
        ):
            write_private(
                self.settings.paths[key],
                json.dumps(
                    {
                        "models": [
                            {
                                **base,
                                "slug": model,
                                "input_modalities": ["text"]
                                if key == "ark_catalog"
                                else ["text", "image"],
                                "context_window": 100000
                                if model == "gpt-6-astra"
                                else 50000,
                            }
                            for model in models
                        ]
                    }
                ),
            )

    def test_new_preset_qualification_and_negative_fast_admission(self):
        from copy import deepcopy

        from loopx_codex_provider_routing.contract import qualify_snapshot

        package = Path(__file__).resolve().parents[1]
        baseline = json.loads(
            (package / "examples" / "qualification-snapshot.json").read_text()
        )["snapshot"]
        observation = deepcopy(baseline)
        observation["routing_preset"] = "abc-sol-astra"
        native = set(ROUTES)
        ark = {
            "ark/deepseek-v4-flash",
            "deepseek-v4-flash",
            "deepseek-v4-flash-ga-260731",
            "deepseek-v4-pro-ga-260813",
        }
        observation["visible_models"] = sorted(VISIBLE_SELECTORS)
        observation["hidden_models"] = sorted((native | ark) - VISIBLE_SELECTORS) + [
            "gpt-5.6-sol",
            "gpt-6-astra",
        ]
        observation["fast_models"] = sorted(
            slug for slug in native if slug.startswith("fast/")
        )
        observation["input_modalities"] = {
            slug: ["text", "image"] if slug in native else ["text"]
            for slug in native | ark
        }
        observation["selector_default_service_tiers"] = {
            slug: "fast" if slug.startswith("fast/") else "default"
            for slug in native | ark
        }
        observation["route_traversal"] = {}
        for slug, route in ROUTES.items():
            observation["route_traversal"][slug] = {
                "entrypoint": "affinity_then_first"
                if slug.removeprefix("fast/").startswith(("auto/", "auto-with-ds/"))
                or slug == "gpt-5.6-luna"
                else f"codex-{route['order'][0]}",
                "ordered_candidates": [f"codex-{slot}" for slot in route["order"]]
                + route["tail"],
                "fallback_tail": route["tail"],
                "max_cycles": 1,
            }
        self.assertTrue(qualify_snapshot(observation)["qualified"])
        visible_regression = deepcopy(observation)
        visible_regression["visible_models"].append("codex-b/gpt-6-astra")
        self.assertFalse(qualify_snapshot(visible_regression)["qualified"])
        broken = deepcopy(observation)
        broken["route_traversal"]["fast/codex-c/gpt-6-astra"][
            "ordered_candidates"
        ].append("ark-text")
        self.assertFalse(qualify_snapshot(broken)["qualified"])
        self.assertTrue(qualify_snapshot(baseline)["qualified"])

    def test_failed_reconcile_does_not_patch_earlier_slots(self):
        from unittest.mock import patch

        self.seed()
        path = self.runtime.AUTH_DIR / "a.json"
        original = path.read_bytes()
        (self.runtime.AUTH_DIR / "c.json").unlink()
        with patch.object(self.runtime, "prepare"), self.assertRaises(ValueError):
            self.runtime.reconcile()
        self.assertEqual(path.read_bytes(), original)

    def test_rollback_rejects_changed_identity_before_writes(self):
        self.seed()
        backup = snapshot(self.runtime)
        write_private(self.runtime.MODEL_CATALOG, '{"new": true}')
        path = self.runtime.AUTH_DIR / "c.json"
        data = json.loads(path.read_text())
        data["account_id"] = "fixture-replaced"
        write_private(path, json.dumps(data))
        with self.assertRaises(ValueError):
            rollback(self.runtime, backup)
        self.assertEqual(self.runtime.MODEL_CATALOG.read_text(), '{"new": true}')

    def test_rollback_deactivates_new_enrollment_without_deleting_tokens(self):
        self.seed()
        write_private(
            self.runtime.SLOTS_FILE, json.dumps({"a": "a.json", "b": "b.json"})
        )
        backup = snapshot(self.runtime)
        write_private(
            self.runtime.SLOTS_FILE,
            json.dumps({"a": "a.json", "b": "b.json", "c": "c.json"}),
        )
        rollback(self.runtime, backup)
        data = json.loads((self.runtime.AUTH_DIR / "c.json").read_text())
        self.assertTrue(data["disabled"])
        self.assertEqual(data["refresh_token"], "fixture-refresh")
        self.assertNotIn("c", self.runtime.load_slots())

    def test_config_fallback_requires_explicit_four_route_opt_in(self):
        config = self.runtime.runtime_config(
            "fixture-only", management_secret="fixture-management"
        )
        self.assertIn('alias: "auto-with-ds/gpt-6-astra"', config)
        self.assertNotIn('alias: "codex-c/gpt-6-astra"', config)
        self.assertNotIn('alias: "fast/', config)
        self.assertNotIn('alias: "gpt-5.6-luna"', config)
        self.assertIn('host: "127.0.0.1"', config)


if __name__ == "__main__":
    unittest.main()
