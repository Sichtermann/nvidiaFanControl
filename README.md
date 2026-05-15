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

On this machine, as checked on May 14, 2026, the system exposes `nzxtsmart2` under `/sys/class/hwmon/hwmon8`, but it does **not** currently expose an `nvidia` hwmon node there. That means the practical direct path today is `NVML`, not `nvidia-smi`.

## Files

- `gpu-fan-control.py`: main controller
- `gpu-fan-control.service`: systemd unit
- `gpu-fan-control.env.example`: optional config file template

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

1. Install the script:

```bash
sudo install -m 0755 gpu-fan-control.py /usr/local/bin/gpu-fan-control.py
```

2. Install the unit:

```bash
sudo install -m 0644 gpu-fan-control.service /etc/systemd/system/gpu-fan-control.service
```

3. Optional: create `/etc/default/gpu-fan-control` from the example file and tune the values.

4. Reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gpu-fan-control.service
```

5. Verify:

```bash
sudo /usr/local/bin/gpu-fan-control.py --print-temp
sudo systemctl status gpu-fan-control.service
journalctl -u gpu-fan-control.service -f
```

## Notes For This Host

- GPU detected: `NVIDIA A100-PCIE-40GB`
- Driver detected: `595.71.05`
- Current live service on this host is using `/mnt/disks/work/dev/servarr/gpu-fan-control.sh`
- Current live temperature source is `nvidia-smi`

If you migrate the live service, update it to point at `gpu-fan-control.py` and move the environment values from the old unit into `/etc/default/gpu-fan-control`.
