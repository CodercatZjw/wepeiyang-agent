from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class AdbError(RuntimeError):
    pass


def _registry_install_dirs() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    results: list[Path] = []
    for key_name in (r"SOFTWARE\BlueStacks_nxt_cn", r"SOFTWARE\BlueStacks_nxt"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_name) as key:
                value, _ = winreg.QueryValueEx(key, "InstallDir")
                results.append(Path(value.strip('"')))
        except OSError:
            pass
    return results


def find_adb(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("WPY_ADB"):
        candidates.append(Path(os.environ["WPY_ADB"]))
    if shutil.which("adb"):
        candidates.append(Path(shutil.which("adb") or ""))

    for install_dir in _registry_install_dirs():
        candidates.extend((install_dir / "HD-Adb.exe", install_dir / "adb.exe"))

    candidates.extend(
        [
            Path(r"C:\Program Files\BlueStacks_nxt_cn\HD-Adb.exe"),
            Path(r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe"),
            Path(r"C:\Program Files\BlueStacks\HD-Adb.exe"),
        ]
    )
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()
    raise AdbError(
        "没有找到 ADB。请安装/启动蓝叠，或用 --adb 指定 HD-Adb.exe；"
        "也可以设置环境变量 WPY_ADB。"
    )


@dataclass(slots=True)
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int


class AdbClient:
    def __init__(self, adb_path: Path, serial: str | None = None, timeout: float = 30):
        self.adb_path = adb_path
        self.serial = serial
        self.timeout = timeout

    def _base(self) -> list[str]:
        command = [str(self.adb_path)]
        if self.serial:
            command.extend(["-s", self.serial])
        return command

    def run(
        self,
        *args: str,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        command = [*self._base(), *args]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout or self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdbError(f"ADB 命令执行失败：{' '.join(command)}\n{exc}") from exc
        result = CommandResult(completed.stdout, completed.stderr, completed.returncode)
        if check and completed.returncode != 0:
            message = (completed.stderr or completed.stdout).decode("utf-8", "replace").strip()
            raise AdbError(f"ADB 返回错误：{message or completed.returncode}")
        return result

    def text(self, *args: str, check: bool = True, timeout: float | None = None) -> str:
        return self.run(*args, check=check, timeout=timeout).stdout.decode("utf-8", "replace")

    def shell(self, *args: str, check: bool = True, timeout: float | None = None) -> str:
        return self.text("shell", *args, check=check, timeout=timeout)

    def devices(self) -> list[tuple[str, str]]:
        output = self.text("devices", "-l")
        found: list[tuple[str, str]] = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                found.append((parts[0], parts[1]))
        return found

    def select_device(self) -> str:
        devices = [(serial, state) for serial, state in self.devices() if state == "device"]
        if self.serial:
            if not any(serial == self.serial for serial, _ in devices):
                raise AdbError(f"指定的设备 {self.serial} 当前不可用。")
            return self.serial
        if not devices:
            raise AdbError(
                "没有可用的安卓设备。请先启动蓝叠，并在 设置 → 高级 中打开 Android 调试桥(ADB)。"
            )
        if len(devices) > 1:
            choices = "、".join(serial for serial, _ in devices)
            raise AdbError(f"检测到多个设备（{choices}），请用 --serial 指定一个。")
        self.serial = devices[0][0]
        return self.serial

    def wait_until_booted(self, seconds: int = 60) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = self.shell("getprop", "sys.boot_completed", check=False).strip()
            if value == "1":
                return
            time.sleep(1)
        raise AdbError("等待安卓模拟器启动超时。")

    def package_installed(self, package: str) -> bool:
        result = self.run("shell", "dumpsys", "package", package, check=False)
        text = (result.stdout + result.stderr).decode("utf-8", "replace")
        return f"Package [{package}]" in text

    def start_app(self, component: str) -> None:
        package = component.split("/", 1)[0]
        self.shell("am", "force-stop", package)
        self.shell("am", "start", "-W", "-n", component, timeout=45)

    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 650) -> None:
        self.shell(
            "input",
            "swipe",
            str(x1),
            str(y1),
            str(x2),
            str(y2),
            str(duration_ms),
        )

    def hierarchy(self) -> bytes:
        remote = "/sdcard/wpy-agent-window.xml"
        self.shell("uiautomator", "dump", "--compressed", remote, timeout=45)
        data = self.run("exec-out", "cat", remote, timeout=30).stdout
        if not data.lstrip().startswith(b"<?xml"):
            raise AdbError("未能读取安卓页面结构；请确认模拟器已解锁且天外天在前台。")
        return data

    def screenshot(self) -> bytes:
        data = self.run("exec-out", "screencap", "-p", timeout=30).stdout
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AdbError("ADB 没有返回有效截图。")
        return data

    def screen_size(self) -> tuple[int, int]:
        output = self.shell("wm", "size")
        match = re.search(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", output)
        if not match:
            raise AdbError(f"无法识别屏幕尺寸：{output.strip()}")
        return int(match.group(1)), int(match.group(2))

