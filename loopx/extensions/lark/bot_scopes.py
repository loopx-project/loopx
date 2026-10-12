"""Recommended Lark (Feishu) API scope bundle for a LoopX project bot.

Each new LoopX Lark bot (e.g. a Goal Channel sender) should apply this bundle
once instead of adding scopes reactively. The bundle is derived from production
Goal Channel, event-inbox, kanban, slash-command, card and docs-comment use.
``core`` covers Goal Channel, reviewer notification and kanban; ``inbox`` adds
message ingestion; ``sink`` adds interactive cards and docs comments.
"""

from __future__ import annotations

from .identity_shapes import LARK_APP_ID_PATTERN as APP_ID_PATTERN

_OFFICIAL_SCOPE_APPLY_HOSTS = ("open.feishu.cn", "open.larkoffice.com")

# 核心：发消息、建群/查群/群成员管理 —— goal-channel、reviewer、kanban 必需
CORE_BOT_SCOPES = (
    "im:message",
    "im:chat:read",
    "im:chat:create",
    "im:chat:update",
    "im:chat.members:read",
    "im:chat.members:write_only",
    "contact:user.base:readonly",
    "contact:contact.base:readonly",
    "application:application:self_manage",
    "application:bot.basic_info:read",
)

# 收件箱：事件订阅收群/单聊消息（敏感，需审核）
INBOX_BOT_SCOPES = (
    "im:message:readonly",
    "im:message.group_msg",
    "im:message.group_msg.include_bot:read",
    "im:message.p2p_msg:readonly",
)

# 交互/文档 sink：指令确认卡片、文档评论
SINK_BOT_SCOPES = (
    "cardkit:card:read",
    "cardkit:card:write",
    "docs:document.comment:read",
    "docs:document.comment:create",
    "docs:document.comment:delete",
)

RECOMMENDED_BOT_SCOPES = CORE_BOT_SCOPES + INBOX_BOT_SCOPES + SINK_BOT_SCOPES


def recommended_bot_scope_apply_url(
    app_id: str, host: str = "open.larkoffice.com"
) -> str:
    """Return the developer-console batch scope-apply URL for one LoopX bot.

    Mirrors the console URL that the Lark platform returns for missing scopes so
    a new bot can apply the whole recommended bundle in one click.
    """
    if not APP_ID_PATTERN.fullmatch(str(app_id or "")):
        raise ValueError("app id must begin with cli_")
    if host not in _OFFICIAL_SCOPE_APPLY_HOSTS:
        raise ValueError("unsupported scope-apply host")
    scopes = ",".join(RECOMMENDED_BOT_SCOPES)
    from urllib.parse import quote

    return f"https://{host}/page/scope-apply?clientID={quote(app_id)}&scopes={quote(scopes)}"
