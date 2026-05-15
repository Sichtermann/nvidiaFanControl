# GPU Fan Control

This repo controls an external fan for a GPU by writing PWM values to an NZXT Smart Device v2 (`nzxtsmart2`). The improved version in this repo avoids polling `nvidia-smi` in a shell loop.

## Why Change It

`nvidia-smi` is a userspace CLI layered on top of NVML. It works, but it is the wrong level for a long-running control loop:

- every sample spawns a process
- parsing CLI output is less robust than calling the driver API directly
- it is slower and adds another failure surface

The better order of preference is:

1. NVIDIA temperature from `sysfs hwmon` if the driver exposes it
2. direct `NVML` calls via `libnvidia-ml.so`
3. no `nvidia-smi` fallback in the controller loop

## Files

- `gpu-fan-control.py`: main controller
- `gpu-fan-control.service`: systemd unit
- `gpu-fan-control.env`: sample install-time config
- `gpu-fan-control.env.example`: optional config file template
- `install-live.sh`: helper script to install the service on a host

## Safety Behavior

The controller is built around fail-safe defaults:

- writes `SAFE_PWM` on startup before entering the loop
- writes `SAFE_PWM` on shutdown and on any control/read failure
- exits after repeated failures so systemd can restart it cleanly
- writes `EMERGENCY_PWM` immediately if temperature reaches `CRITICAL_TEMP`
- uses systemd watchdog heartbeats so a stuck controller is restarted
- treats unexpectedly low fan RPM at high PWM as a fault and pushes emergency PWM
- uses an exclusive lock in `/run/gpu-fan-control/lock`
- prefers kernel `sysfs` temperature when available, otherwise uses direct `NVML`

## Installation

1. Review and tune the config:

```bash
cp gpu-fan-control.env.example gpu-fan-control.env
```

Edit `gpu-fan-control.env` for your hardware before installing.

2. Install the script:

```bash
sudo install -m 0755 gpu-fan-control.py /usr/local/bin/gpu-fan-control.py
```

3. Install the unit:

```bash
sudo install -m 0644 gpu-fan-control.service /etc/systemd/system/gpu-fan-control.service
```

4. Install the environment file:

```bash
sudo install -m 0644 gpu-fan-control.env /etc/default/gpu-fan-control
```

5. Reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gpu-fan-control.service
```

6. Verify:

```bash
sudo /usr/local/bin/gpu-fan-control.py --print-temp
sudo systemctl status gpu-fan-control.service
journalctl -u gpu-fan-control.service -f
```

## Configuration Notes

- `NZXT_HWMON_NAME`, `PWM_CHANNEL`, and `FAN_CHANNEL` must match your controller's hwmon layout under `/sys/class/hwmon`.
- The controller prefers `sysfs` temperature from `NVIDIA_HWMON_NAME=nvidia` when available, otherwise it falls back to `NVML`.
- If your system has multiple GPUs, set `GPU_INDEX` or uncomment and set `GPU_PCI_BUS_ID` for a stable target.
- `SAFE_PWM` is applied on startup, shutdown, and repeated read/control failures.
- `EMERGENCY_PWM` is applied when the GPU reaches `CRITICAL_TEMP` or fan feedback indicates a likely fault.

## Quick Install

If you want a single command install flow on a host, tune `gpu-fan-control.env` first and then run:

```bash
sudo ./install-live.sh
```
