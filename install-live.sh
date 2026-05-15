#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "Installing GPU fan control from: $REPO_DIR"

run_step() {
  local description="$1"
  shift

  echo "$description"
  if "$@"; then
    return 0
  fi

  local status=$?
  echo "Failed: $description (exit $status)" >&2
  return "$status"
}

install -d /usr/local/bin
install -d /etc/default
install -d /etc/systemd/system

if [[ -f /etc/systemd/system/gpu-fan-control.service ]]; then
  cp -a /etc/systemd/system/gpu-fan-control.service "/etc/systemd/system/gpu-fan-control.service.bak-${STAMP}"
  echo "Backed up existing unit to /etc/systemd/system/gpu-fan-control.service.bak-${STAMP}"
fi

if [[ -f /etc/default/gpu-fan-control ]]; then
  cp -a /etc/default/gpu-fan-control "/etc/default/gpu-fan-control.bak-${STAMP}"
  echo "Backed up existing env file to /etc/default/gpu-fan-control.bak-${STAMP}"
fi

install -m 0755 "$REPO_DIR/gpu-fan-control.py" /usr/local/bin/gpu-fan-control.py
install -m 0644 "$REPO_DIR/gpu-fan-control.env" /etc/default/gpu-fan-control
install -m 0644 "$REPO_DIR/gpu-fan-control.service" /etc/systemd/system/gpu-fan-control.service

run_step "Preflight: reading GPU temperature" timeout 10s /usr/local/bin/gpu-fan-control.py --print-temp

run_step "Preflight: applying safe PWM" timeout 10s /usr/local/bin/gpu-fan-control.py --set-safe-pwm

run_step "Reloading systemd units" timeout 60s systemctl daemon-reload
run_step "Restarting gpu-fan-control.service" timeout 30s systemctl restart gpu-fan-control.service
run_step "Enabling gpu-fan-control.service" systemctl enable gpu-fan-control.service

echo
echo "Verification:"
echo "  /usr/local/bin/gpu-fan-control.py --print-temp"
/usr/local/bin/gpu-fan-control.py --print-temp || true
echo
echo "  systemctl --no-pager --full status gpu-fan-control.service"
systemctl --no-pager --full status gpu-fan-control.service || true
echo
echo "  journalctl -u gpu-fan-control.service -n 20 --no-pager"
journalctl -u gpu-fan-control.service -n 20 --no-pager || true
