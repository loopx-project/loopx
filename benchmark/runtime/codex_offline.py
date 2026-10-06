"""Offline Codex staging shared by Harbor benchmark adapters."""

import os
import shlex
from pathlib import Path

from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment

_DEFAULT_OFFLINE_DIR = str(
    Path(__file__).resolve().parents[1] / "swe-marathon" / "codex"
)

_STAGE_DIR = "/tmp/codex-offline"

_RETRY_FLAGS = (
    "-c model_providers.harbor.name=harbor"
    " -c model_providers.harbor.request_max_retries=8"
    " -c model_providers.harbor.stream_max_retries=8"
    " -c model_providers.harbor.stream_idle_timeout_ms=300000"
)


class CodexOffline(Codex):
    def __init__(self, *args, goals="false", web_search="disabled", **kwargs):
        if str(goals) not in {"true", "false"}:
            raise ValueError("goals must be true or false")
        if web_search not in {"disabled", "cached", "live"}:
            raise ValueError("unsupported web_search setting")
        self._goals = str(goals)
        self._web_search = web_search
        super().__init__(*args, **kwargs)

    @staticmethod
    def name() -> str:
        return "codex-offline"

    def version(self) -> str | None:
        return self._version or "offline"

    def get_version_command(self) -> str | None:
        return "/usr/local/bin/codex --version"

    def build_cli_flags(self) -> str:
        flags = super().build_cli_flags()
        return (
            f"{flags} -c features.goals={self._goals}"
            f' -c web_search="{self._web_search}" {_RETRY_FLAGS}'
        ).strip()

    async def install(self, environment: BaseEnvironment) -> None:
        offline_dir = Path(os.environ.get("CODEX_OFFLINE_DIR", _DEFAULT_OFFLINE_DIR))
        codex_bin = (offline_dir / "codex").resolve()
        rg_bin = (offline_dir / "rg").resolve()
        sidecar_bin = (offline_dir / "codex-code-mode-host").resolve()
        bwrap_bin = offline_dir / "codex-resources" / "bwrap"
        if not codex_bin.is_file():
            raise FileNotFoundError(
                f"离线 codex 二进制不存在: {codex_bin}。"
                " 用 stage_codex_offline.sh 从宿主机的 @openai/codex 包里取出来。"
            )
        if not sidecar_bin.is_file():
            raise FileNotFoundError(
                f"codex-code-mode-host 不存在: {sidecar_bin}。"
                " 重跑 stage_codex_offline.sh —— 旧版脚本只抠 codex 和 rg，"
                " 缺 sidecar 会让容器里的工具面静默全废。"
            )

        await self.exec_as_root(environment, command=f"mkdir -p {_STAGE_DIR}")

        await environment.upload_file(codex_bin, f"{_STAGE_DIR}/codex")
        await environment.upload_file(sidecar_bin, f"{_STAGE_DIR}/codex-code-mode-host")
        if rg_bin.is_file():
            await environment.upload_file(rg_bin, f"{_STAGE_DIR}/rg")
        if bwrap_bin.is_file():
            await environment.upload_file(bwrap_bin, f"{_STAGE_DIR}/bwrap")

        await self.exec_as_root(
            environment,
            command=(
                "set -eu; "
                f"install -m 0755 {_STAGE_DIR}/codex /usr/local/bin/codex; "
                f"install -m 0755 {_STAGE_DIR}/codex-code-mode-host "
                "  /usr/local/bin/codex-code-mode-host; "
                f"if [ -f {_STAGE_DIR}/rg ]; then "
                f"  install -m 0755 {_STAGE_DIR}/rg /usr/local/bin/rg; "
                "fi; "
                f"if [ -f {_STAGE_DIR}/bwrap ]; then "
                f"  install -m 0755 {_STAGE_DIR}/bwrap /usr/local/bin/bwrap; "
                "fi; "
                f"rm -rf {_STAGE_DIR}; "
                "mkdir -p /logs/agent; "
                f"chown {shlex.quote(str(environment.default_user or 'root'))} /logs/agent; "
                "{ /usr/local/bin/codex --version; "
                "  md5sum /usr/local/bin/codex /usr/local/bin/codex-code-mode-host; "
                "} > /logs/agent/codex_version.txt 2>&1; "
                "cat /logs/agent/codex_version.txt"
            ),
        )

        self.logger.info(
            f"codex 离线安装完成（来源 {offline_dir}，含 code-mode sidecar）"
        )
