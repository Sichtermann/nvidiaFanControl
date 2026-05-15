#!/usr/bin/env python3

import argparse
import ctypes
import ctypes.util
import fcntl
import os
import signal
import socket
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


@dataclass
class Config:
    nzxt_name: str = os.environ.get("NZXT_HWMON_NAME", "nzxtsmart2")
    nvidia_name: str = os.environ.get("NVIDIA_HWMON_NAME", "nvidia")
    pwm_channel: int = env_int("PWM_CHANNEL", 1)
    fan_channel: int = env_int("FAN_CHANNEL", 1)
    min_temp: int = env_int("MIN_TEMP", 35)
    max_temp: int = env_int("MAX_TEMP", 75)
    critical_temp: int = env_int("CRITICAL_TEMP", 82)
    min_pwm: int = env_int("MIN_PWM", 18)
    max_pwm: int = env_int("MAX_PWM", 200)
    safe_pwm: int = env_int("SAFE_PWM", 200)
    emergency_pwm: int = env_int("EMERGENCY_PWM", 255)
    sleep_interval: int = env_int("SLEEP_INTERVAL", 5)
    temp_window: int = env_int("TEMP_WINDOW", 6)
    max_consecutive_errors: int = env_int("MAX_CONSECUTIVE_ERRORS", 3)
    pwm_step: int = env_int("PWM_STEP", 5)
    min_pwm_delta: int = env_int("MIN_PWM_DELTA", 5)
    min_rpm: int = env_int("MIN_RPM", 700)
    rpm_check_pwm: int = env_int("RPM_CHECK_PWM", 120)
    max_consecutive_rpm_errors: int = env_int("MAX_CONSECUTIVE_RPM_ERRORS", 3)
    log_interval: int = env_int("LOG_INTERVAL", 12)
    gpu_index: int = env_int("GPU_INDEX", 0)
    gpu_pci_bus_id: Optional[str] = os.environ.get("GPU_PCI_BUS_ID")
    runtime_dir: Path = Path(os.environ.get("RUNTIME_DIRECTORY", "/run/gpu-fan-control"))


def log(level: str, message: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [{level}] {message}", flush=True)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def find_hwmon_by_name(name: str) -> Optional[Path]:
    for entry in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            if (entry / "name").read_text().strip() == name:
                return entry
        except OSError:
            continue
    return None


class SystemdNotifier:
    def __init__(self) -> None:
        self.socket_path = os.environ.get("NOTIFY_SOCKET")

    def notify(self, message: str) -> None:
        if not self.socket_path:
            return

        address = self.socket_path
        if address.startswith("@"):
            address = "\0" + address[1:]

        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode("utf-8"))


class FanFeedbackError(RuntimeError):
    pass


class NzxtController:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.hwmon = find_hwmon_by_name(cfg.nzxt_name)
        if self.hwmon is None:
            raise RuntimeError(f"NZXT hwmon '{cfg.nzxt_name}' not found")

        self.pwm_path = self.hwmon / f"pwm{cfg.pwm_channel}"
        self.pwm_enable_path = self.hwmon / f"pwm{cfg.pwm_channel}_enable"
        self.pwm_mode_path = self.hwmon / f"pwm{cfg.pwm_channel}_mode"
        self.fan_input_path = self.hwmon / f"fan{cfg.fan_channel}_input"

        if not self.pwm_path.exists():
            raise RuntimeError(f"PWM path not found: {self.pwm_path}")

    def prepare(self) -> None:
        if self.pwm_enable_path.exists():
            current = self.pwm_enable_path.read_text().strip()
            if current != "1":
                self.pwm_enable_path.write_text("1\n")
        if self.pwm_mode_path.exists():
            mode = self.pwm_mode_path.read_text().strip()
            if mode != "1":
                log("WARN", f"{self.pwm_mode_path} is {mode}, expected 1")

    def set_pwm(self, value: int) -> None:
        self.pwm_path.write_text(f"{clamp(value, 0, 255)}\n")

    def read_rpm(self) -> Optional[int]:
        if not self.fan_input_path.exists():
            return None
        try:
            return int(self.fan_input_path.read_text().strip())
        except (OSError, ValueError):
            return None


class SysfsTemperatureReader:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.temp_path = self._discover()
        self.source_name = f"sysfs:{self.temp_path}" if self.temp_path else None

    def _discover(self) -> Optional[Path]:
        hwmon = find_hwmon_by_name(self.cfg.nvidia_name)
        if hwmon is None:
            return None

        temp_path = hwmon / "temp1_input"
        if temp_path.exists():
            return temp_path
        return None

    def read_celsius(self) -> int:
        if self.temp_path is None:
            raise RuntimeError("NVIDIA hwmon temperature path not available")
        milli_c = int(self.temp_path.read_text().strip())
        if milli_c < 0 or milli_c > 120_000:
            raise RuntimeError(f"invalid sysfs temperature: {milli_c}")
        return milli_c // 1000


class NvmlTemperatureReader:
    NVML_TEMPERATURE_GPU = 0

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.lib = self._load_library()
        self._bind()
        self.initialized = False
        self.device = ctypes.c_void_p()
        self.source_name = "nvml"

    def _load_library(self):
        libname = ctypes.util.find_library("nvidia-ml") or "libnvidia-ml.so.1"
        try:
            return ctypes.CDLL(libname)
        except OSError as exc:
            raise RuntimeError(f"failed to load NVML ({libname}): {exc}") from exc

    def _bind(self) -> None:
        self.lib.nvmlInit_v2.restype = ctypes.c_int
        self.lib.nvmlShutdown.restype = ctypes.c_int
        self.lib.nvmlErrorString.restype = ctypes.c_char_p
        self.lib.nvmlErrorString.argtypes = [ctypes.c_int]
        self.lib.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
        self.lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
        self.lib.nvmlDeviceGetHandleByPciBusId_v2.restype = ctypes.c_int
        self.lib.nvmlDeviceGetHandleByPciBusId_v2.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        self.lib.nvmlDeviceGetTemperature.restype = ctypes.c_int
        self.lib.nvmlDeviceGetTemperature.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]

    def _check(self, code: int, func: str) -> None:
        if code == 0:
            return
        error = self.lib.nvmlErrorString(code)
        message = error.decode("utf-8", "replace") if error else f"error {code}"
        raise RuntimeError(f"{func} failed: {message}")

    def initialize(self) -> None:
        self._check(self.lib.nvmlInit_v2(), "nvmlInit_v2")
        self.initialized = True
        if self.cfg.gpu_pci_bus_id:
            bus_id = self.cfg.gpu_pci_bus_id.encode("ascii")
            self._check(
                self.lib.nvmlDeviceGetHandleByPciBusId_v2(bus_id, ctypes.byref(self.device)),
                "nvmlDeviceGetHandleByPciBusId_v2",
            )
            self.source_name = f"nvml:{self.cfg.gpu_pci_bus_id}"
        else:
            self._check(
                self.lib.nvmlDeviceGetHandleByIndex_v2(self.cfg.gpu_index, ctypes.byref(self.device)),
                "nvmlDeviceGetHandleByIndex_v2",
            )
            self.source_name = f"nvml:index{self.cfg.gpu_index}"

    def read_celsius(self) -> int:
        if not self.initialized:
            self.initialize()
        temp = ctypes.c_uint()
        self._check(
            self.lib.nvmlDeviceGetTemperature(
                self.device,
                self.NVML_TEMPERATURE_GPU,
                ctypes.byref(temp),
            ),
            "nvmlDeviceGetTemperature",
        )
        if temp.value > 120:
            raise RuntimeError(f"invalid NVML temperature: {temp.value}")
        return int(temp.value)

    def close(self) -> None:
        if self.initialized:
            self.lib.nvmlShutdown()
            self.initialized = False


class TemperatureSource:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.reader = None
        self.source_name = ""
        sysfs_reader = SysfsTemperatureReader(cfg)
        if sysfs_reader.temp_path is not None:
            self.reader = sysfs_reader
            self.source_name = sysfs_reader.source_name or "sysfs"
            return

        nvml_reader = NvmlTemperatureReader(cfg)
        nvml_reader.initialize()
        self.reader = nvml_reader
        self.source_name = nvml_reader.source_name

    def read_celsius(self) -> int:
        if self.reader is None:
            raise RuntimeError("no temperature backend available")
        return self.reader.read_celsius()

    def close(self) -> None:
        if isinstance(self.reader, NvmlTemperatureReader):
            self.reader.close()


class Controller:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.nzxt = NzxtController(cfg)
        self.temp_source = TemperatureSource(cfg)
        self.temperatures = deque(maxlen=max(1, cfg.temp_window))
        self.last_pwm: Optional[int] = None
        self.lock_file = None
        self.notifier = SystemdNotifier()
        self.stopping = False

    def acquire_lock(self) -> None:
        self.cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.cfg.runtime_dir / "lock"
        self.lock_file = lock_path.open("w")
        fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lock_file.write(f"{os.getpid()}\n")
        self.lock_file.flush()

    def release_lock(self) -> None:
        if self.lock_file is not None:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self.lock_file.close()
                self.lock_file = None

    def prepare(self) -> None:
        if self.cfg.min_temp >= self.cfg.max_temp:
            raise RuntimeError("MIN_TEMP must be less than MAX_TEMP")
        if self.cfg.max_temp >= self.cfg.critical_temp:
            raise RuntimeError("CRITICAL_TEMP must be greater than MAX_TEMP")
        if self.cfg.min_pwm > self.cfg.max_pwm:
            raise RuntimeError("MIN_PWM must be less than or equal to MAX_PWM")
        self.nzxt.prepare()
        self.acquire_lock()

    def cleanup(self) -> None:
        self.temp_source.close()
        self.release_lock()

    def set_safe_pwm(self, reason: str) -> None:
        self.nzxt.set_pwm(self.cfg.safe_pwm)
        self.last_pwm = self.cfg.safe_pwm
        log("WARN", f"Applied safe PWM {self.cfg.safe_pwm} ({reason})")

    def set_emergency_pwm(self, reason: str) -> None:
        self.nzxt.set_pwm(self.cfg.emergency_pwm)
        self.last_pwm = self.cfg.emergency_pwm
        log("ERROR", f"Applied emergency PWM {self.cfg.emergency_pwm} ({reason})")

    def compute_control_temp(self, raw_temp: int) -> int:
        self.temperatures.append(raw_temp)
        avg_temp = sum(self.temperatures) // len(self.temperatures)
        return max(raw_temp, avg_temp)

    def compute_pwm(self, temp: int) -> int:
        if temp <= self.cfg.min_temp:
            pwm = self.cfg.min_pwm
        elif temp >= self.cfg.max_temp:
            pwm = self.cfg.max_pwm
        else:
            span_temp = self.cfg.max_temp - self.cfg.min_temp
            span_pwm = self.cfg.max_pwm - self.cfg.min_pwm
            pwm = self.cfg.min_pwm + ((temp - self.cfg.min_temp) * span_pwm // span_temp)

        step = max(1, self.cfg.pwm_step)
        pwm = ((pwm + step // 2) // step) * step
        return clamp(pwm, 0, 255)

    def apply_pwm(self, pwm: int) -> None:
        if self.last_pwm is not None and abs(pwm - self.last_pwm) < self.cfg.min_pwm_delta:
            return
        self.nzxt.set_pwm(pwm)
        self.last_pwm = pwm

    def sample(self) -> tuple[int, int, int]:
        raw_temp = self.temp_source.read_celsius()
        control_temp = self.compute_control_temp(raw_temp)
        pwm = self.compute_pwm(control_temp)
        return raw_temp, control_temp, pwm

    def check_rpm(self, pwm: int) -> Optional[int]:
        rpm = self.nzxt.read_rpm()
        if rpm is None or pwm < self.cfg.rpm_check_pwm:
            return rpm
        if rpm < self.cfg.min_rpm:
            raise FanFeedbackError(f"fan rpm too low: rpm={rpm}, pwm={pwm}")
        return rpm

    def run(self) -> None:
        self.prepare()
        self.set_safe_pwm("startup")
        consecutive_errors = 0
        consecutive_rpm_errors = 0
        cycle = 0

        log("INFO", f"NZXT hwmon: {self.nzxt.hwmon}")
        log("INFO", f"Temperature backend: {self.temp_source.source_name}")
        log(
            "INFO",
            (
                f"Curve: min_temp={self.cfg.min_temp} max_temp={self.cfg.max_temp} "
                f"critical_temp={self.cfg.critical_temp} min_pwm={self.cfg.min_pwm} "
                f"max_pwm={self.cfg.max_pwm}"
            ),
        )
        self.notifier.notify("READY=1")

        try:
            while True:
                try:
                    raw_temp, control_temp, pwm = self.sample()
                    consecutive_errors = 0

                    if raw_temp >= self.cfg.critical_temp:
                        self.set_emergency_pwm(f"critical temperature {raw_temp}C")
                    else:
                        self.apply_pwm(pwm)

                    try:
                        rpm = self.check_rpm(self.last_pwm or pwm)
                        consecutive_rpm_errors = 0
                    except Exception as exc:
                        consecutive_rpm_errors += 1
                        self.set_emergency_pwm(f"fan feedback failure: {exc}")
                        if consecutive_rpm_errors >= self.cfg.max_consecutive_rpm_errors:
                            raise FanFeedbackError(
                                f"too many consecutive fan feedback failures ({consecutive_rpm_errors})"
                            ) from exc
                        rpm = self.nzxt.read_rpm()

                    if cycle % max(1, self.cfg.log_interval) == 0:
                        rpm_part = f", rpm={rpm}" if rpm is not None else ""
                        log(
                            "INFO",
                            f"temp={raw_temp}C control_temp={control_temp}C pwm={self.last_pwm}{rpm_part}",
                        )
                    self.notifier.notify("WATCHDOG=1")
                except Exception as exc:
                    consecutive_errors += 1
                    if isinstance(exc, FanFeedbackError):
                        self.set_emergency_pwm(f"fan feedback failure: {exc}")
                    else:
                        self.set_safe_pwm(f"read/control failure: {exc}")
                    if consecutive_errors >= self.cfg.max_consecutive_errors:
                        raise RuntimeError(
                            f"too many consecutive control failures ({consecutive_errors})"
                        ) from exc
                    self.notifier.notify("WATCHDOG=1")

                cycle += 1
                time.sleep(self.cfg.sleep_interval)
        finally:
            self.cleanup()


def install_signal_handlers(controller: Controller) -> None:
    def handle_signal(signum, _frame) -> None:
        controller.stopping = True
        try:
            controller.set_safe_pwm(f"signal {signum}")
        finally:
            controller.cleanup()
        raise SystemExit(0)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGQUIT):
        signal.signal(sig, handle_signal)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GPU fan control using sysfs or NVML")
    parser.add_argument("--set-safe-pwm", action="store_true", help="write SAFE_PWM and exit")
    parser.add_argument("--set-emergency-pwm", action="store_true", help="write EMERGENCY_PWM and exit")
    parser.add_argument("--print-temp", action="store_true", help="print one temperature sample and exit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cfg = Config()

    if args.set_safe_pwm:
        nzxt = NzxtController(cfg)
        nzxt.prepare()
        nzxt.set_pwm(cfg.safe_pwm)
        log("INFO", f"Applied safe PWM {cfg.safe_pwm}")
        return 0

    if args.set_emergency_pwm:
        nzxt = NzxtController(cfg)
        nzxt.prepare()
        nzxt.set_pwm(cfg.emergency_pwm)
        log("INFO", f"Applied emergency PWM {cfg.emergency_pwm}")
        return 0

    if args.print_temp:
        source = TemperatureSource(cfg)
        try:
            temp = source.read_celsius()
            print(f"{source.source_name} {temp}")
        finally:
            source.close()
        return 0

    controller = Controller(cfg)
    install_signal_handlers(controller)

    try:
        controller.run()
    except BlockingIOError:
        log("ERROR", "another gpu-fan-control instance is already running")
        return 1
    except Exception as exc:
        try:
            controller.set_safe_pwm(f"fatal error: {exc}")
        except Exception:
            pass
        log("ERROR", str(exc))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
