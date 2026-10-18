#!/usr/bin/env bash
# Install agentloop for the current user. Does not enable the timer (see RUNBOOK.md).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
install -d -m 700 ~/.config/agentloop ~/.local/state/agentloop
install -d ~/.local/share/agentloop ~/.config/systemd/user
install -m 755 "$SRC/agentloop.py" "$SRC/selftest.py" ~/.local/share/agentloop/
install -m 644 "$SRC/agentloop.service" "$SRC/agentloop.timer" ~/.config/systemd/user/
systemctl --user daemon-reload
echo "installed; timer NOT enabled"
