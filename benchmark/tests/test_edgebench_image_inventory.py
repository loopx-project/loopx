from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from benchmark.edgebench.image_inventory import main


def test_inventory_keeps_image_revisions_and_legacy_receipts_separate(tmp_path, capsys):
    old_image, new_image = "sha256:" + "a" * 64, "sha256:" + "b" * 64

    def record(root, run, *, task="case", task_sha="c" * 64, image_id=None, size=None):
        receipt = root / "runs" / run / task / "runtime-receipt.json"
        receipt.parent.mkdir(parents=True)
        body = {"task": task, "task_sha256": task_sha, "run_id": run,
                "private_field": "never export this"}
        if image_id is not None:
            image = {"image_id": image_id, "logical_size_bytes": size}
            body["local_docker_images"] = {"work": image, "judge": image}
        receipt.write_text(json.dumps(body))
        return receipt

    first, second = tmp_path / "first", tmp_path / "second"
    record(first, "legacy")
    record(first, "observed", image_id=old_image, size=100)
    record(second, "another", image_id=old_image, size=None)
    record(second, "retagged", image_id=new_image, size=250)
    record(second, "new-task", task_sha="d" * 64, image_id=old_image, size=100)
    record(first, "ignored", task="other", image_id=new_image, size=200)
    record(first, "unreadable").write_text("{")

    main(["--log-dir", str(first), "--log-dir", str(second), "--log-dir", str(first), "--task", "case"])
    output = capsys.readouterr().out
    assert "never export this" not in output
    result = json.loads(output)
    assert result["unreadable_receipts"] == 1
    original, revised = result["cases"]
    assert original["task_sha256"] == "c" * 64
    assert original["receipts_with_preflight"] == 3
    assert original["receipts_without_preflight"] == 1
    assert revised["task_sha256"] == "d" * 64
    assert revised["receipts_with_preflight"] == 1
    assert {row["role"] for row in original["images"]} == {"judge", "work"}
    assert [row for row in original["images"] if row["role"] == "work"] == [
        {"role": "work", "image_id": old_image, "logical_sizes_bytes": [100],
         "observations": 2, "observations_without_size": 1},
        {"role": "work", "image_id": new_image, "logical_sizes_bytes": [250],
         "observations": 1, "observations_without_size": 0},
    ]


def test_preflight_looks_up_each_required_image_once_and_keeps_missing_image_gate(tmp_path):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from docker.errors import ImageNotFound
    from benchmark.runtime.sforge_backend import RecordingDockerBackend

    queries = []

    def get(key):
        queries.append(key)
        if key == "missing":
            raise ImageNotFound("missing")
        return SimpleNamespace(id="sha256:" + "a" * 64, attrs={"Size": 42} if key == "shared" else {})

    backend = RecordingDockerBackend(log_dir=tmp_path, logger=None, oauth_proxy=True,
                                    client=SimpleNamespace(images=SimpleNamespace(get=get)))
    observed = backend.observe_local_images(work_key="shared", judge_key="shared")
    assert queries == ["shared"]
    assert observed["work"] == observed["judge"] == {
        "image_id": "sha256:" + "a" * 64, "logical_size_bytes": 42,
    }
    observed = backend.observe_local_images(work_key="shared", judge_key="old-engine")
    assert observed["judge"]["logical_size_bytes"] is None
    with pytest.raises(RuntimeError, match="Missing native image: missing"):
        backend.observe_local_images(work_key="shared", judge_key="missing")
