"""Real disposable Todo preview/apply and status; no model or active Goal access."""
import json
import os
from pathlib import Path
import runpy
import signal
import sys
import tempfile

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.effect_runtime import restart_effect_runtime

root = Path(sys.argv[1]).resolve()
tempfile.tempdir = str(root)
for variable in ("TMPDIR", "TEMP", "TMP"):
    os.environ[variable] = str(root)
fixtures = runpy.run_path(str(Path(__file__).resolve().parents[1] / "loopx-chat-actions-smoke.py"))
registry, state = fixtures["write_registry_fixture"](root)
data = json.loads(registry.read_text())
data["goals"][0].update(id="progress-projection", display_name="Synthetic workspace")
data["goals"][0]["coordination"]["handoff_mode"] = "soft_claim"
editing = len(sys.argv) > 2 and sys.argv[2] == "requirements"
if editing:
    data["goals"][0]["coordination"]["registered_agents"] = ["agent-a"]
registry.write_text(json.dumps(data), encoding="utf-8")
runtime = Path(data["common_runtime_root"])
canonical_fixtures = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "tests/control_plane/canonical_authority_fixture.py")
)
canonical_fixtures["initialize_canonical_authority"](
    runtime,
    "progress-projection",
    build_todo_runtime_shadow_projection(
        goal_id="progress-projection",
        handoff_mode="soft_claim",
        todos=([{"schema_version": "todo_item_v0", "todo_id": "todo_requirements_source", "index": 1,
            "role": "agent", "status": "open", "done": False, "archive_state": "active",
            "source_section": "Agent Todo", "task_class": "advancement_task", "claimed_by": "agent-a",
            "text": "数据导出交付要求：保留原始研究记录、来源和版本，生成可复查的导出文件。 "
                + "一、输入必须来自声明的研究记录，不得把缺失、失败或尚未验证的结果填成零。保留原始标识和引用；明确时间窗口，注明采集时刻与执行时刻的区别。 "
                + "二、导出必须覆盖全部已选记录，按稳定顺序分页，重试不得重复或漏掉记录。长内容完整保留，列表摘要不能替代正文。 "
                + "三、每项效果判断都带理由与来源，观察、解释和实际采用分别展示。没有证据的关系保持未知，不从相邻时间或任务数量推断收益。 "
                + "四、修改任务要求时保存当前版本，原有验收过期后应明确显示；不因保存要求而启动新任务、接管其它工作或自动增加资源。 "
                + "五、暂时断线、响应丢失和并发修改必须保留草稿和原操作身份。失败时说明是否已经提交，优先读回原结果，不能通过重复执行消除不确定性。 "
                + "六、文件读回包含可用内容、原来源及适用版本。真实工作、测试样例、历史缓存和未完成验证要有明确边界。 "
                + "七、普通用户只需表达修改并保存，不填写内部版本、租约键或协议字段。仅在出现真实冲突或缺少必要身份时请求额外决定。 "
                + "八、在桌面、窄屏与键盘路径验证相同的任务，错误信息无需横向滚动；返回原任务后仍能看到完整要求与实际保存结果。 "
                + "九、所有原生状态以权威来源读回为准，页面存在、测试通过和提交记录不能代表已安装或完成全部目标。 "
                + "十、交付说明保留未完成事项和下一负责人，沿原任务推进，避免为了制造进度而复制分支、任务或相同资料。 "
                + "FINAL_ACCEPTANCE: preserve every requirement and verify the exact stored source."}] if editing else []),
    ),
    state_path=state,
    provider="file",
)
server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
server.registry_path, server.runtime_root, server.runtime_root_override = registry, runtime, str(runtime)
server.selected_goal_id, server.scan_roots, server.limit = "progress-projection", [], 20
server.action_store = ChatActionStore(root / "actions")
server.action_service = ChatActionService(store=server.action_store, registry_path=registry, runtime_root_override=str(runtime))
server.verbose = False
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
print(json.dumps({"port": server.server_port, "registry": str(registry)}), flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
    restart_effect_runtime()
