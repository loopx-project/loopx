from __future__ import annotations

PERFORMANCE_DIAGNOSIS_CATALOG_ENTRY = {
    "id": "performance-diagnosis",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "title": "Local performance diagnosis with scoped profiling tools",
    "status": "active-preview",
    "default_enabled": False,
    "documentation": {"source_root": "loopx/capabilities/performance_diagnosis",
                      "site_root": "capabilities/performance-diagnosis", "canonical": "README.md"},
    "real_world_anchor": "A slow CLI or provider operation whose observed stacks must be separated from uninstrumented acceptance measurements.",
    "user_value": "Select a suitable profiler, preserve exact argv, and inspect bounded hotspots without claiming execution, root cause, or provider admission.",
    "entry_command": "loopx performance-diagnosis plan --help",
    "commands": [
        {"command": "loopx performance-diagnosis plan --tool pyinstrument --command-json command.json --output-directory .local/profile",
         "purpose": "Construct an explicit capture recipe; the Host owns tool installation and execution.", "write_boundary": "read-only"},
        {"command": "loopx performance-diagnosis inspect --profile-json .local/profile/profile.speedscope.json",
         "purpose": "Inspect independent recorded profiles with self and inclusive hotspots.", "write_boundary": "read-only; local-private output, never uploaded"},
    ],
    "implemented_protocols": [
        {"schema_version": version, "module": "loopx.control_plane.capabilities.performance_diagnosis",
         "doc": "loopx/capabilities/performance_diagnosis/README.md"}
        for version in ("performance_diagnosis_plan_v0", "performance_diagnosis_observation_v0")
    ],
    "smokes": ["node --experimental-strip-types --test tests/control_plane_ts/performance_diagnosis.test.ts",
               "python -m pytest tests/capabilities/test_performance_diagnosis.py -q"],
    "next_real_step": "Run the managed performance diagnosis workflow on an owned disposable target, then retest the original uninstrumented workload.",
}
