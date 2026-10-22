"""Real disposable SQLite/packaged UI journey; no model or live Goal writes.

Run with an empty fixture directory and a free loopback port. Pass
--without-check-plan to exercise default review and failed-read recovery. The ordinary
Agent refinement uses the same native CLI as an external host after upgrade.
An explicit --assets-dir selects a frozen packaged UI for a UI counterfactual;
the default still requires a source-current bundle. Optional --overview-empty,
--overview-sparse and --overview-readability exercise declared source states;
they never create an execution or a passing acceptance result.
"""
import json
from pathlib import Path
import sys

from loopx.chat_server import serve_chat
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "control_plane"))
from canonical_authority_fixture import initialize_canonical_authority

root = Path(sys.argv[1])
readability = "--overview-readability" in sys.argv[3:]
empty = "--overview-empty" in sys.argv[3:]
sparse = "--overview-sparse" in sys.argv[3:]
root.mkdir(parents=True, exist_ok=True)
project = root / "project"
project.mkdir()
state = project / "state.md"
state.write_text("---\nstatus: active\nobjective: Explain the product clearly\n---\n# Product launch\n\n"
    "## Objective\nExplain the product clearly. Keep accuracy, clarity and the approved introduction.\n\n"
    "## Agent Todo\n\n")
if readability or sparse:
    state.write_text("---\nstatus: active\nobjective: 交付清晰可靠的产品体验\n---\n# 产品体验改进\n\n"
        "## Objective\n交付清晰可靠的产品体验，让用户看清当前进展、需要处理的事项与可核查的成果。\n\n## Agent Todo\n\n")
if empty:
    state.write_text("---\nstatus: active\n---\n# Product launch\n\n## Agent Todo\n\n")
registry, runtime = root / "registry.json", root / "runtime"
write_fixture_registry(project=project, registry_path=registry, runtime_root=runtime,
    goal_id="product-launch", domain="product", adapter_kind="generic_project_goal_v0",
    state_file=str(state), registered_agents=["author"])
todo = {"schema_version": "todo_item_v0", "todo_id": "todo_tour", "role": "agent",
    "text": "Produce and improve the product presentation", "status": "open", "done": False,
    "task_class": "advancement_task", "action_kind": "implement", "index": 1,
    "source_section": "Agent Todo", "archive_state": "active", "claimed_by": "author"}
initialize_canonical_authority(runtime, "product-launch", build_todo_runtime_shadow_projection(
    goal_id="product-launch", todos=[] if empty else [todo], handoff_mode="soft_claim"), state_path=state, provider="sqlite")

def cli(*args):
    code, value = run_json_cli_result("goal-acceptance", *args, "--goal-id", "product-launch",
        registry_path=registry, runtime_root=runtime)
    if code:
        raise RuntimeError(value)
    return value

if not (empty or sparse):
    for index, (criterion, description) in enumerate([
        ("tour-opening", "Retain the approved introduction"),
        ("tour-order", "Show delegation before the task board"),
    ]):
        document = {"outcome_id": "product-tour", "owner_agent_id": "author", "objective": "Deliver a clear product tour",
            "requirements": [{"id": criterion, "description": description}], "todo_ids": ["todo_tour"],
            "source": {"ref": f"message:refinement-{index}", "summary": description}}
        path = root / f"refinement-{index}.json"
        path.write_text(json.dumps(document))
        cli("refine", "--agent-id", "author", "--document", str(path), "--operation-id", f"refine-{index}",
            "--expected-provider-revision", cli("inspect")["provider_revision"], "--execute")

    (project / "tour.txt").write_text("Delegation\nTask board\n")
    (project / "check-tour.py").write_text(
        "from pathlib import Path\nimport sys\ntext = Path('tour.txt').read_text()\n"
        "if sys.argv[1] == 'opening': assert text.startswith('Introduction\\n')\n"
        "else: assert text.index('Delegation') < text.index('Task board')\n"
    )
    review = {"outcome_id": "product-tour", "source": {"ref": "artifact:review", "summary": "Reviewed the first presentation"},
        "review": [
            {"id": "tour-opening", "conclusion": "unmet", "evidence_refs": ["tour.txt"], "note": "The introduction is still missing"},
            {"id": "tour-order", "conclusion": "met", "evidence_refs": ["tour.txt"], "note": "Delegation precedes the task board"},
        ]}
    path = root / "review.json"
    path.write_text(json.dumps(review))
    cli("refine", "--agent-id", "author", "--document", str(path), "--operation-id", "review",
        "--expected-provider-revision", cli("inspect")["provider_revision"], "--execute")
    if readability:
        # A populated overview through the real declaration owner. No injected
        # acceptance status or copied user material: native reviews/checks decide it.
        for index, objective in enumerate([
            "升级与恢复：保留可用版本，失败时给出明确的恢复路径",
            "工作区：让用户看清当前进展、等待事项与成果",
            "交接文档：无需翻找历史消息即可继续工作",
            "输入与反馈：保留草稿，准确显示操作结果",
            "发布体验：验证实际入口和可追溯的交付物",
        ]):
            descriptions = [
                "普通入口打开后即可看清当前工作与已交付结果，重要信息保持可见，完整说明和原始证据按需展开，不让重复的状态与方法说明挤占首屏。",
                "读取失败时显示真实的未知状态，并提供只读重试；保留已经输入的草稿和原操作身份，恢复后不会重复写入或静默覆盖先前结果。",
                "在桌面和手机宽度验证完整用户流程，键盘、焦点和触屏都能操作；折叠明细不改变原有要求，也不能隐藏失败和仍需处理的事项。",
                "保留适用版本、来源和决定性证据，清楚区分作者自查、独立检查与实际采用；一次成功的读取或合并不等于整体目标已经完成。",
                "使用现有的配置和执行边界，权限不足或状态变化时明确停止对应操作，不通过新增会话、复制任务或修改其他人的配置来绕过限制。",
                "提交可审阅的实现与验证证据，报告已通过、失败和未测试的边界；重新打开页面后能读回同一结果，完整历史仍可访问。",
            ]
            patch = {"outcome_id": f"experience-{index}", "owner_agent_id": "author", "objective": objective,
                "requirements": [{"id": f"experience-{index}-{number}", "description": description} for number, description in enumerate(descriptions)],
                "todo_ids": ["todo_tour"], "source": {"ref": f"fixture:experience-{index}", "summary": objective}}
            path = root / f"experience-{index}.json"
            path.write_text(json.dumps(patch, ensure_ascii=False))
            cli("refine", "--agent-id", "author", "--document", str(path), "--operation-id", f"experience-{index}",
                "--expected-provider-revision", cli("inspect")["provider_revision"], "--execute")
            if index < 4:
                patch = {"outcome_id": f"experience-{index}", "source": {"ref": f"fixture:review-{index}", "summary": "Review of the synthetic delivery"},
                    "review": [{"id": f"experience-{index}-{number}", "conclusion": "unmet" if number == 0 else "uncertain",
                        "evidence_refs": [] if number == 5 else ["tour.txt"],
                        "note": "尚无可核验的交付证据。" if number == 5 else "当前交付物尚未覆盖这项要求，保留缺口并继续核对实际用户入口。"} for number in range(6)]}
                path.write_text(json.dumps(patch, ensure_ascii=False))
                cli("refine", "--agent-id", "author", "--document", str(path), "--operation-id", f"experience-review-{index}",
                    "--expected-provider-revision", cli("inspect")["provider_revision"], "--execute")
    path = root / "checks.json"
    path.write_text(json.dumps({"outcome_id": "product-tour", "criteria": [
        {"id": "tour-opening", "validation_argv": ["python3", "check-tour.py", "opening"]},
        {"id": "tour-order", "validation_argv": ["python3", "check-tour.py", "order"]},
    ]}))
    if "--without-check-plan" not in sys.argv[3:]:
        cli("prepare", "--agent-id", "author", "--document", str(path), "--operation-id", "check-plan", "--execute")

print(json.dumps({"ready": True, "port": int(sys.argv[2]), "registry": str(registry), "runtime": str(runtime)}), flush=True)
serve_chat(registry_path=registry, runtime_root_override=runtime, scan_roots=[project],
    port=int(sys.argv[2]), codex_bin="unavailable-synthetic-host", claude_bin="unavailable-synthetic-host",
    assets_dir=Path(sys.argv[sys.argv.index("--assets-dir") + 1]) if "--assets-dir" in sys.argv else None)
