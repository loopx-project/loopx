#!/bin/sh
# Stands in for `claude -p "/usage"` in tests. Counts runs when FAKE_CLAUDE_USAGE_LOG is set,
# so a test can tell cached answers from fresh probes (each line ends with the arguments it got). FAKE_CLAUDE_SESSION_USED sets the session share.
# `claude auth status` answers as the real one does, with the plan it reports.
# Session and "all models" weekly limits refer to the same account-wide pool.
if [ "$1" = "auth" ]; then
  printf '{"loggedIn":true,"authMethod":"claude.ai","subscriptionType":"%s"}\n' "${FAKE_CLAUDE_PLAN:-max}"
  exit 0
fi
[ -n "$FAKE_CLAUDE_USAGE_LOG" ] && echo "$(date -u +%FT%TZ) $*" >> "$FAKE_CLAUDE_USAGE_LOG"
cat <<EOF
You are currently using your subscription to power your Claude Code usage

Current session: ${FAKE_CLAUDE_SESSION_USED:-95}% used · resets Oct 10, 12:40pm (UTC)
Current week (all models): 40% used · resets Oct 11, 7am (UTC)

What's contributing to your limits usage?
Approximate, based on local sessions on this machine.
EOF
