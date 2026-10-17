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
# Codex only offers its full tool surface (web_search, apply_patch, update_plan,
# write_stdin) to slugs in its model catalog. Gateway aliases such as
# gpt-5.6-sol are unknown to the shipped binary, so a catalog file must be
# staged next to the offline binary and referenced via model_catalog_json.
MODEL_CATALOG_REMOTE = "/usr/local/share/codex/model-catalog.json"

_RETRY_FLAGS = (
    "-c model_providers.harbor.name=harbor"
    " -c model_providers.harbor.request_max_retries=8"
    " -c model_providers.harbor.stream_max_retries=8"
    " -c model_providers.harbor.stream_idle_timeout_ms=300000"
)


async def stage_portable_python(environment: BaseEnvironment, destination: str) -> None:
    """Stage the configured runner interpreter without using the task PATH."""
    source = Path(os.environ["LOOPX_PORTABLE_PYTHON"]).resolve()
    await environment.upload_dir(source, destination)


class CodexOffline(Codex):
    def __init__(self, *args, goals="false", web_search="disabled", **kwargs):
        if str(goals) not in {"true", "false"}:
            raise ValueError("goals must be true or false")
        if web_search not in {"disabled", "cached", "live"}:
            raise ValueError("unsupported web_search setting")
        self._goals = str(goals)
        self._web_search = web_search
        self._model_catalog_staged = False
        self._model_catalog_source: Path | None = None
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
        catalog = offline_dir / "model-catalog.json"
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
        if catalog.is_file():
            await environment.upload_file(catalog, f"{_STAGE_DIR}/model-catalog.json")
            self._model_catalog_staged = True
            self._model_catalog_source = catalog.resolve()

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
                f"if [ -f {_STAGE_DIR}/model-catalog.json ]; then "
                f"  install -D -m 0644 {_STAGE_DIR}/model-catalog.json {MODEL_CATALOG_REMOTE}; "
                "fi; "
                f"rm -rf {_STAGE_DIR}; "
                "mkdir -p /logs/agent; "
                f"chown {shlex.quote(str(environment.default_user or 'root'))} /logs/agent; "
                "{ /usr/local/bin/codex --version; "
                "  md5sum /usr/local/bin/codex /usr/local/bin/codex-code-mode-host; "
                f"  [ -f {MODEL_CATALOG_REMOTE} ] && md5sum {MODEL_CATALOG_REMOTE} || echo 'model-catalog: none'; "
                "} > /logs/agent/codex_version.txt 2>&1; "
                "cat /logs/agent/codex_version.txt"
            ),
        )

        self.logger.info(
            f"codex 离线安装完成（来源 {offline_dir}，含 code-mode sidecar，"
            f"model catalog {'已装' if self._model_catalog_staged else '无'}）"
        )
