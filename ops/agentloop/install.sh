#!/usr/bin/env bash
# Install agentloop as a sandboxed system service. Run with sudo. Does NOT enable the timer.
#   sudo ops/agentloop/install.sh                  install or update code, user, units
#   sudo ops/agentloop/install.sh --claude-token   store the Claude subscription token (paste, hidden)
#   sudo ops/agentloop/install.sh --uninstall      remove everything (keeps nothing)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
OWNER="${SUDO_USER:?run through sudo from the owner account}"

# A narrow terminal wraps the setup token; copying one screen line gave a 79-character token and an API 401.
# Join every pasted line, and drain the rest of the paste so no part of it reaches the shell afterwards.
read_token() {
  local line="" t=""
  IFS= read -rs line || true
  t="$line"; line=""
  while IFS= read -rs -t 1 line || [ -n "$line" ]; do t+="$line"; line=""; done
  printf '%s' "$t"
}

# Floor, not an exact length: current tokens are about 108 characters, a truncated copy was 79.
TOKEN_MIN_LEN=100
token_ok() {
  [[ "$1" =~ ^sk-ant-oat[0-9A-Za-z_-]+$ ]] && [ "${#1}" -ge "$TOKEN_MIN_LEN" ]
}

if [ "${1:-}" = "--claude-token" ]; then
  install -d -m 700 /etc/agentloop
  printf "Paste the FULL token printed by 'claude setup-token', all wrapped lines (input hidden), then Enter: "
  t="$(read_token)"; echo
  if ! token_ok "$t"; then
    echo "rejected (${#t} characters): expected sk-ant-oat… with no spaces and at least $TOKEN_MIN_LEN characters;" \
         "a short value is usually a copy of only the first wrapped line. Nothing was stored."
    unset t; exit 1
  fi
  umask 077; printf '%s\n' "$t" > /etc/agentloop/claude-token
  echo "stored /etc/agentloop/claude-token (root, 0600, ${#t} characters)"; unset t; exit 0
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
