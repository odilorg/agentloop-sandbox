#!/usr/bin/env bash
# Install agentloop as a sandboxed system service. Run with sudo. Does NOT enable the timer.
#   sudo ops/agentloop/install.sh                  install or update code, user, units
#   sudo ops/agentloop/install.sh --claude-token   store the Claude subscription token (paste, hidden)
#   sudo ops/agentloop/install.sh --uninstall      remove everything (keeps nothing)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
OWNER="${SUDO_USER:?run through sudo from the owner account}"

if [ "${1:-}" = "--claude-token" ]; then
  install -d -m 700 /etc/agentloop
  read -rsp "Paste the token printed by 'claude setup-token' (input hidden): " t; echo
  [[ "$t" =~ ^sk-ant-oat[0-9A-Za-z_-]+$ ]] || { echo "that does not look like a setup-token token"; exit 1; }
  umask 077; printf '%s\n' "$t" > /etc/agentloop/claude-token; unset t
  echo "stored /etc/agentloop/claude-token (root, 0600)"; exit 0
fi

if [ "${1:-}" = "--uninstall" ]; then
  systemctl disable --now agentloop.timer 2>/dev/null || true
  rm -f /etc/systemd/system/agentloop@.service /etc/systemd/system/agentloop.timer
  systemctl daemon-reload
  rm -rf /opt/agentloop /etc/agentloop /etc/agentloop.json /var/lib/agentloop /var/log/agentloop /var/lib/agentloop-inbox
  userdel agentloop 2>/dev/null || true
  echo "agentloop removed"; exit 0
fi

id agentloop >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /var/lib/agentloop \
  --shell /usr/sbin/nologin --user-group agentloop
install -d -m 755 -o root -g root /opt/agentloop /opt/agentloop/bin
install -m 644 -o root -g root "$SRC/agentloop.py" "$SRC/selftest.py" "$SRC/isolation.py" /opt/agentloop/
# Pinned copy of the owner's current Claude Code binary (the unit cannot see /home; no auto-update).
CLAUDE="$(readlink -f "$(sudo -u "$OWNER" bash -lc 'command -v claude')")"
install -m 755 -o root -g root "$CLAUDE" /opt/agentloop/bin/claude
install -d -m 700 -o root -g root /etc/agentloop
# Inbox: the owner writes start.json / STOP here; the service can only read it.
install -d -m 2750 -o "$OWNER" -g agentloop /var/lib/agentloop-inbox   # setgid: new files get group agentloop
install -m 644 "$SRC/agentloop@.service" "$SRC/agentloop.timer" /etc/systemd/system/
systemctl daemon-reload
echo "installed: user agentloop, /opt/agentloop (claude $(/opt/agentloop/bin/claude --version 2>/dev/null | cut -d' ' -f1)), units; timer NOT enabled"
