"""Disposable SQLite/HTTP reader for the packaged requirements search journey."""

import sys
import json
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))


def main():
    from pytest import MonkeyPatch
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.test_delivery_review import make_project
    from loopx.control_plane.goals.acceptance import (
        inspect_goal_acceptance,
        refine_goal_outcome,
    )
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.control_plane.effect_runtime import restart_effect_runtime

    with (
        tempfile.TemporaryDirectory(prefix="requirements-real-") as directory,
        MonkeyPatch.context() as patch,
    ):
        root = Path(directory)
        isolate_sqlite_runtime(root, patch)
        registry, runtime = make_project(root, provider="sqlite")
        config = json.loads(registry.read_text())
        config["goals"][0]["coordination"] = {
            "registered_agents": ["author", "builder", "reviewer"]
        }
        registry.write_text(json.dumps(config))
        basis = dict(
            registry_path=registry, goal_id="release-demo", runtime_root=str(runtime)
        )
        for index in range(5):
            result = refine_goal_outcome(
                **basis,
                agent_id="author",
                execute=True,
                expected_provider_revision=inspect_goal_acceptance(**basis)[
                    "provider_revision"
                ],
                operation_id=f"seed-{index}",
                refinement={
                    "outcome_id": f"result-{index}",
                    "owner_agent_id": "author",
                    "objective": f"Retained result {index}",
                    "source": {
                        "ref": f"message:owner-{index}",
                        "summary": "Preserve source evidence",
                    },
                    "requirements": [
                        {
                            "id": f"req-{i}",
                            "description": f"Retain source {index}-{i}. 保留完整要求。",
                        }
                        for i in range(30)
                    ],
                    "review": [
                        {
                            "id": f"req-{i}",
                            "conclusion": "uncertain",
                            "note": f"核验修订 {index}-{i}",
                            "evidence_refs": [f"artifact:source-{index}-{i}"],
                        }
                        for i in range(30)
                    ],
                },
            )
            assert result["status"] == "applied", result
        before = inspect_goal_acceptance(**basis)

        class Handler(ChatRequestHandler):
            def _goal_acceptance_inspect(self):
                if self.server.fault:
                    self._send_error("Synthetic source unavailable", status=503)
                else:
                    super()._goal_acceptance_inspect()

        server = ChatHTTPServer(("127.0.0.1", 0), Handler)
        server.registry_path, server.runtime_root, server.runtime_root_override = (
            registry,
            runtime,
            str(runtime),
        )
        server.assets_dir = REPO / "loopx/web/chat"
        server.scan_roots, server.limit, server.selected_goal_id, server.verbose = (
            [],
            20,
            None,
            False,
        )
        server.fault = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(
            json.dumps({"origin": f"http://127.0.0.1:{server.server_port}"}), flush=True
        )
        try:
            for command in sys.stdin:
                if command.strip() == "inspect":
                    print(
                        json.dumps(
                            {"unchanged": before == inspect_goal_acceptance(**basis)}
                        ),
                        flush=True,
                    )
                else:
                    server.fault = command.strip() == "fault"
                    print(json.dumps({"fault": server.fault}), flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            restart_effect_runtime(wait_for_exit=True)


if __name__ == "__main__":
    main()
