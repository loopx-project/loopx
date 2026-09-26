"""Connector registry core: catalog, durable state, usage, value ranking."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ...paths import select_default_runtime_root


CONNECTOR_REGISTRY_SCHEMA_VERSION = "connector_registry_v1"
LAYER_LABELS = {
    "L0": "infra",
    "L1": "official",
    "L2": "capital-flow",
    "L3": "financial",
    "L4": "sentiment-alt",
    "L5": "paid-professional",
}
VALUE_BASE = {"P0": 10.0, "P1": 6.0, "P2": 3.0}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


BUILTIN_CONNECTOR_CATALOG: list[dict[str, Any]] = [
    # L0 基础设施
    {"id": "sina-daily", "name": "新浪日线行情", "layer": "L0", "kind": "market_data",
     "status": "supported", "credential": None, "value_tier": "P1",
     "purpose": "A股日线/技术信号（sina_utils / fetch_daily / signals）"},
    {"id": "ths-financial", "name": "同花顺财务摘要", "layer": "L3", "kind": "financial",
     "status": "supported", "credential": None, "value_tier": "P1",
     "purpose": "resilience/filing 财务观察（fetch_financials）"},
    {"id": "ark-web-search", "name": "ARK 联网搜索", "layer": "L0", "kind": "search",
     "status": "supported", "credential": "ark", "value_tier": "P1",
     "purpose": "web_search 查询规划+综合（ark_search.py，密钥仅进程内）"},
    {"id": "ego-browser", "name": "ego-browser 网页抓取", "layer": "L0", "kind": "fetch",
     "status": "supported", "credential": None, "value_tier": "P1",
     "purpose": "真浏览器检索/抓取（公告核实等）"},
    {"id": "openviking", "name": "OpenViking 记忆/向量", "layer": "L0", "kind": "memory",
     "status": "supported", "credential": None, "value_tier": "P2",
     "purpose": "reward-memory / 经验召回"},
    {"id": "lark-notify", "name": "飞书通知", "layer": "L0", "kind": "notification",
     "status": "supported", "credential": "lark", "value_tier": "P1",
     "purpose": "goal-channel 候选通知与确认"},
    # L1 官方披露
    {"id": "cninfo-announcement", "name": "巨潮公告", "layer": "L1", "kind": "announcement",
     "status": "pending", "credential": None, "value_tier": "P0",
     "purpose": "官方公告流 → filing/事件标记（接口已实测可达）"},
    {"id": "exchange-disclosures", "name": "交易所披露（问询/监管函）", "layer": "L1",
     "kind": "announcement", "status": "pending", "credential": None, "value_tier": "P0",
     "purpose": "问询/处罚等监管事件"},
    # L2 资金流
    {"id": "capital-flow-lhb", "name": "龙虎榜", "layer": "L2", "kind": "capital_flow",
     "status": "pending", "credential": None, "value_tier": "P0",
     "purpose": "机构席位异动 → 时机确认"},
    {"id": "capital-flow-margin", "name": "两融余额", "layer": "L2", "kind": "capital_flow",
     "status": "pending", "credential": None, "value_tier": "P1",
     "purpose": "杠杆资金异动"},
    {"id": "capital-flow-connect", "name": "沪深港通持股", "layer": "L2", "kind": "capital_flow",
     "status": "pending", "credential": None, "value_tier": "P1",
     "purpose": "北向持股变化"},
    # L4 舆情/另类
    {"id": "cls-telegraph", "name": "财联社电报", "layer": "L4", "kind": "news",
     "status": "blocked", "credential": None, "value_tier": "P2",
     "purpose": "快讯（仅确认信号）", "blocker": "抓取条款/合规"},
    {"id": "xueqiu-sentiment", "name": "雪球/股吧情绪", "layer": "L4", "kind": "sentiment",
     "status": "blocked", "credential": None, "value_tier": "P2",
     "purpose": "情绪确认信号", "blocker": "反爬/条款"},
    {"id": "baidu-index", "name": "百度指数热度", "layer": "L4", "kind": "alt",
     "status": "pending", "credential": None, "value_tier": "P2",
     "purpose": "关注度（另类）"},
    {"id": "wechat-articles", "name": "微信公众号信源", "layer": "L4", "kind": "news",
     "status": "pending", "credential": None, "value_tier": "P2",
     "purpose": "行业/公司深度"},
    # L5 付费/专业
    {"id": "wind-ifind", "name": "Wind/iFinD/Choice", "layer": "L5", "kind": "financial",
     "status": "blocked", "credential": "paid", "value_tier": "P2",
     "purpose": "专业财务/数据", "blocker": "账号/预算"},
    {"id": "tushare-pro", "name": "Tushare 积分", "layer": "L5", "kind": "financial",
     "status": "blocked", "credential": "paid", "value_tier": "P2",
     "purpose": "结构化数据", "blocker": "积分/预算"},
]


def default_registry_path() -> Path:
    runtime_root = Path(os.environ.get("LOOPX_RUNTIME_ROOT") or select_default_runtime_root())
    return runtime_root / "connector-registry.json"


def _catalog_index() -> dict[str, dict[str, Any]]:
    return {c["id"]: dict(c) for c in BUILTIN_CONNECTOR_CATALOG}


def _fresh_usage(connector: Mapping[str, Any]) -> dict[str, Any]:
    tier = str(connector.get("value_tier") or "P2")
    return {"count": 0, "ok": 0, "fail": 0, "total_ms": 0, "last_used_at": None,
            "score": VALUE_BASE.get(tier, 3.0), "manual_note": None}


def _new_state() -> dict[str, Any]:
    connectors = [dict(c) for c in BUILTIN_CONNECTOR_CATALOG]
    usage = {c["id"]: _fresh_usage(c) for c in connectors}
    return {"schema_version": CONNECTOR_REGISTRY_SCHEMA_VERSION, "updated_at": _now_iso(),
            "connectors": connectors, "usage": usage}


def load_connector_registry(path: Path | None = None) -> dict[str, Any]:
    state_path = path or default_registry_path()
    state = _new_state()
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("connectors"), list):
                state = raw
        except (OSError, ValueError):
            pass
    # Re-sync against the builtin catalog while preserving local operator state.
    stored_connectors = {
        str(c.get("id")): dict(c)
        for c in state.get("connectors", [])
        if isinstance(c, Mapping) and c.get("id")
    }
    merged_connectors: list[dict[str, Any]] = []
    usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
    merged_usage: dict[str, dict[str, Any]] = {}
    for c in BUILTIN_CONNECTOR_CATALOG:
        connector_id = c["id"]
        merged = {**dict(c), **(stored_connectors.get(connector_id) or {}), "id": connector_id}
        merged_connectors.append(merged)
        merged_usage[connector_id] = {**(_fresh_usage(merged)), **(usage.get(connector_id) or {})}
    seen = {c["id"] for c in BUILTIN_CONNECTOR_CATALOG}
    for stored in state.get("connectors", []):
        if not isinstance(stored, Mapping):
            continue
        cid = str(stored.get("id") or "")
        if cid and cid not in seen:
            merged_connectors.append(dict(stored))
            merged_usage[cid] = usage.get(cid) or _fresh_usage(stored)
    return {"schema_version": CONNECTOR_REGISTRY_SCHEMA_VERSION, "updated_at": state.get("updated_at"),
            "connectors": merged_connectors, "usage": merged_usage}


def save_connector_registry(state: Mapping[str, Any], path: Path | None = None) -> Path:
    state_path = path or default_registry_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(dict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    return state_path


def _score(usage: Mapping[str, Any], connector: Mapping[str, Any]) -> float:
    tier = str(connector.get("value_tier") or "P2")
    base = VALUE_BASE.get(tier, 3.0)
    status = str(connector.get("status") or "pending")
    status_penalty = 0.0 if status == "supported" else 2.0
    return round(base - status_penalty + 0.2 * int(usage.get("ok") or 0)
                 - 0.5 * int(usage.get("fail") or 0), 2)


def rank_connectors(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for c in state.get("connectors", []):
        u = state.get("usage", {}).get(c["id"], {})
        rows.append({**c, "score": _score(u, c), "usage": u})
    rows.sort(key=lambda r: (-r["score"], r["value_tier"], r["id"]))
    return rows


def list_connectors(state: Mapping[str, Any]) -> dict[str, Any]:
    ranked = rank_connectors(state)
    supported = [r for r in ranked if r["status"] == "supported"]
    pending = [r for r in ranked if r["status"] == "pending"]
    blocked = [r for r in ranked if r["status"] == "blocked"]
    return {
        "schema_version": CONNECTOR_REGISTRY_SCHEMA_VERSION,
        "ok": True,
        "ranked": ranked,
        "supported": supported,
        "pending": pending,
        "blocked": blocked,
        "summary": {"supported": len(supported), "pending": len(pending), "blocked": len(blocked)},
    }


def register_connector(
    state: Mapping[str, Any],
    connector_id: str,
    *,
    name: str | None = None,
    layer: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    credential: str | None = None,
    value_tier: str | None = None,
    purpose: str | None = None,
    blocker: str | None = None,
) -> dict[str, Any]:
    connectors = list(state.get("connectors", []))
    entry = next((c for c in connectors if c.get("id") == connector_id), None)
    if entry is None:
        entry = {"id": connector_id, "status": "pending", "value_tier": "P2",
                 "layer": layer or "L0", "name": name or connector_id}
        connectors.append(entry)
    if name is not None:
        entry["name"] = name
    if layer is not None:
        entry["layer"] = layer
    if kind is not None:
        entry["kind"] = kind
    if status is not None:
        entry["status"] = status
    if credential is not None:
        entry["credential"] = credential
    if value_tier is not None:
        entry["value_tier"] = value_tier
    if purpose is not None:
        entry["purpose"] = purpose
    if blocker is not None:
        entry["blocker"] = blocker
    usage = dict(state.get("usage", {}))
    usage.setdefault(connector_id, _fresh_usage(entry))
    return {"schema_version": CONNECTOR_REGISTRY_SCHEMA_VERSION, "ok": True,
            "connector": entry, "usage": usage[connector_id],
            "connectors": connectors, "usage_map": usage,
            "updated_at": _now_iso()}


def record_connector_use(
    state: Mapping[str, Any],
    connector_id: str,
    *,
    ok: bool = True,
    ms: int = 0,
    manual_note: str | None = None,
) -> dict[str, Any]:
    connectors = list(state.get("connectors", []))
    entry = next((c for c in connectors if c.get("id") == connector_id), None)
    if entry is None:
        raise KeyError(f"unknown connector: {connector_id}")
    usage = {k: dict(v) for k, v in (state.get("usage", {}) or {}).items()}
    u = usage.setdefault(connector_id, _fresh_usage(entry))
    u["count"] = int(u.get("count") or 0) + 1
    u["ok"] = int(u.get("ok") or 0) + (1 if ok else 0)
    u["fail"] = int(u.get("fail") or 0) + (0 if ok else 1)
    u["total_ms"] = int(u.get("total_ms") or 0) + int(ms or 0)
    u["last_used_at"] = _now_iso()
    if manual_note is not None:
        u["manual_note"] = manual_note
    u["score"] = _score(u, entry)
    return {"schema_version": CONNECTOR_REGISTRY_SCHEMA_VERSION, "ok": True,
            "connector_id": connector_id, "usage": u, "score": u["score"],
            "connectors": connectors, "usage_map": usage, "updated_at": _now_iso()}
