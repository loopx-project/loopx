"""Configuration Settings compares two snapshots through the existing owner."""
import copy
import http.client
import json
import threading

from loopx.capabilities.configuration_backup import compare_configuration_backups
from loopx.control_plane.effect_runtime import effect_runtime_result


def test_live_comparison_is_read_only_and_recovers_from_invalid_file(tmp_path):
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = tmp_path / "registry.json"
    server.runtime_root = tmp_path / "runtime"
    server.verbose = False
    server.registry_path.write_text('{"goals":[]}')
    before = effect_runtime_result("configuration.backup", {"action": "capture", "data": {
        "machine_configuration": None,
        "goals": [{"goal_id": "synthetic-before", "goal_configuration": {"id": "synthetic-before", "name": "Old scope"}}],
    }})
    after = effect_runtime_result("configuration.backup", {"action": "capture", "data": {
        "machine_configuration": None,
        "goals": [{"goal_id": "synthetic-after", "goal_configuration": {"id": "synthetic-after", "name": "Reviewed scope"}}],
    }})
    initial = server.registry_path.read_bytes()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(body):
        connection = http.client.HTTPConnection(*server.server_address, timeout=30)
        try:
            connection.request("POST", "/api/chat/configuration-backup/compare", json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        damaged = copy.deepcopy(after)
        damaged["sha256"] = "changed"
        assert post({"before": before, "after": damaged})[0] == 400
        assert post({"before": before, "after": after, "execute": True})[0] == 400
        assert post({"before": before})[0] == 400
        status, result = post({"before": before, "after": after})
        assert status == 200
        assert result == compare_configuration_backups(before, after)
        assert result["only_in_before"] == ["synthetic-before"]
        assert result["only_in_after"] == ["synthetic-after"]
        assert result["written"] is False and result["activation_performed"] is False
        assert server.registry_path.read_bytes() == initial
        assert not (server.runtime_root / "backups").exists()
        assert post({"before": after, "after": after})[1]["change_count"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
