#!/usr/bin/env bash
# Shrinks hub.db after tool-snapshot collapse (docs/tool-output-storage-design.md). Deleting rows
# leaves free pages the daemon reuses, so the file stops growing right away; a VACUUM is what
# actually returns the space, and it needs the daemon stopped. Run this from anywhere:
#
#   scripts/compact-hub-db.sh --apply
#
# It schedules a detached job (a systemd transient unit, outside the daemon's cgroup so the stop
# cannot kill it) that waits for the one-time migration to finish, waits until every thread is
# idle, then stops the service, vacuums, starts it and checks health. Log:
# $XDG_STATE_HOME/calli/compact.log
#
#   scripts/compact-hub-db.sh        vacuum an already-stopped hub's db directly
set -euo pipefail

NAME=calli
# The service's name before the rename to Calli; an installation not moved yet still runs it.
if ! systemctl --user cat "$NAME" >/dev/null 2>&1 && systemctl --user cat remote-agent-hub >/dev/null 2>&1; then
  NAME=remote-agent-hub
fi
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/$NAME"
LOG="$STATE_DIR/compact.log"
ENV_FILE="$(systemctl --user show -p Environment --value "$NAME" | tr ' ' '\n' | sed -nE 's/^(CALLI|RAH)_ENV_FILE=//p' | head -1)"
ENV_FILE="${ENV_FILE:-$HOME/.config/$NAME/.env}"
env_raw() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -1 | sed -E "s/^['\"]|['\"]$//g"; }
# CALLI_<name>, else RAH_<name> from before the rename.
env_get() { local v; v="$(env_raw "CALLI_$1")"; [ -n "$v" ] || v="$(env_raw "RAH_$1")"; echo "$v"; }
HOST="$(env_get HOST)"; HOST="${HOST:-127.0.0.1}"
PORT="$(env_get PORT)"; PORT="${PORT:-8787}"
TOKEN="$(env_get TOKEN)"
case "$HOST" in 0.0.0.0 | ::) HOST=127.0.0.1 ;; esac
BASE="http://$HOST:$PORT"
DATA_DIR="$(env_get DATA_DIR)"; DATA_DIR="${DATA_DIR:-$STATE_DIR/data}"
DB="$DATA_DIR/hub.db"
NODE="$(command -v node)"

say() { echo "[$(date '+%H:%M/%m-%d')] $*"; }

if [ "${1:-}" != "--apply" ]; then
  [ -f "$DB" ] || { echo "no hub.db at $DB" >&2; exit 1; }
  echo "vacuuming $DB ($(du -h "$DB" | cut -f1)); the hub must be stopped"
  "$NODE" -e '
    const { DatabaseSync } = require("node:sqlite");
    const db = new DatabaseSync(process.argv[1]);
    db.exec("PRAGMA wal_checkpoint(TRUNCATE);");
    db.exec("VACUUM;");
    db.close();
  ' "$DB"
  echo "done: $(du -h "$DB" | cut -f1)"
  exit 0
fi

systemctl --user cat "$NAME" >/dev/null 2>&1 || { echo "No $NAME service." >&2; exit 1; }
if systemctl --user cat calli-compact >/dev/null 2>&1; then
  echo "A compact job is already scheduled (unit calli-compact). Log: $LOG"
  exit 0
fi
mkdir -p "$STATE_DIR"

# The job runs as its own transient unit: stopping the daemon must not kill the vacuum.
systemd-run --user --unit=calli-compact --collect \
  env REPO="$REPO" NAME="$NAME" BASE="$BASE" TOKEN="$TOKEN" DB="$DB" LOG="$LOG" NODE="$NODE" \
  "$NODE" -e '
    const { execFileSync, spawnSync } = require("child_process");
    const { DatabaseSync } = require("node:sqlite");
    const say = (m) => { const line = `[${new Date().toISOString()}] ${m}`; console.log(line); require("fs").appendFileSync(process.env.LOG, line + "\n"); };
    const sysctl = (args) => spawnSync("systemctl", ["--user", ...args], { encoding: "utf8" });
    const sleep = (s) => new Promise((r) => setTimeout(r, s));
    (async () => {
      try {
        say("waiting for the migration to finish");
        let flag = false;
        for (let i = 0; i < 240 && !flag; i++) {
          await sleep(30000);
          try {
            const db = new DatabaseSync(process.env.DB, { readOnly: true });
            const row = db.prepare("SELECT value FROM hub_settings WHERE key = ?").get("toolOutputCollapsed");
            db.close();
            flag = row !== undefined && JSON.parse(row.value) === true;
          } catch { /* hub restarting or busy; poll on */ }
        }
        if (!flag) { say("migration did not finish in 2h; giving up, run --apply again"); process.exit(1); }
        say("waiting for every thread to go idle");
        for (;;) {
          let running = "?";
          try {
            const lists = await Promise.all(["/api/threads", "/api/threads?archived=1"].map(async (p) => {
              const res = await fetch(process.env.BASE + p, { headers: { Authorization: `Bearer ${process.env.TOKEN}` } });
              return res.ok ? res.json() : [];
            }));
            running = lists.flat().filter((t) => t.status === "running").length;
          } catch { running = "?"; }
          if (running === 0) break;
          if (running !== "?") say(`${running} thread(s) still running`);
          await sleep(15000);
        }
        say("stopping the daemon for the vacuum");
        sysctl(["stop", process.env.NAME]);
        await sleep(3000);
        say("vacuuming " + process.env.DB);
        const db = new DatabaseSync(process.env.DB);
        db.exec("PRAGMA wal_checkpoint(TRUNCATE);");
        db.exec("VACUUM;");
        db.close();
        say("starting the daemon");
        sysctl(["start", process.env.NAME]);
        for (let i = 0; i < 30; i++) {
          await sleep(2000);
          try {
            const res = await fetch(process.env.BASE + "/api/health");
            if (res.ok) { say("up again: " + (await res.text())); process.exit(0); }
          } catch { /* not up yet */ }
        }
        say("daemon did not come back; check journalctl --user -u " + process.env.NAME);
        process.exit(1);
      } catch (err) {
        say("failed: " + (err && err.message));
        spawnSync("systemctl", ["--user", "start", process.env.NAME], { encoding: "utf8" });
        process.exit(1);
      }
    })();
  '
echo "Scheduled: the db is vacuumed at the next idle window, after the migration finishes."
echo "Progress: $LOG"
