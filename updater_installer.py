"""独立更新器：从临时目录运行，等待主程序退出后备份并替换程序文件。

本文件会被复制到临时目录执行，因此只依赖标准库；不可 import 项目内其他模块。
开发模式：pythonw updater_installer.py --payload payload.json
冻结模式：主程序把打包好的 _internal/updater/updater.exe 复制到临时目录后运行。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

APP_MUTEX_DEFAULT = r"Local\YxyAssistant.App"
UPDATING_MUTEX_DEFAULT = r"Local\YxyAssistant.Updating"
WAIT_APP_EXIT_TIMEOUT = 240.0
APP_EXECUTABLE = "dgut-bot.exe"

# 用户数据：永不覆盖、永不删除。
PRESERVE = (
    "config.json", "auth.json", "account.json", "browser_profile", "签到记录.md",
    "browser-launcher.log", "browser-service.log", ".update", "update-result.json",
    "update_failures.json", ".git", "优学院手机端源码",
)
# 体积大且可再生成的程序目录：直接删除，不做备份（回滚后由静态前端模式兜底）。
DELETABLE = ("__pycache__", "web/node_modules", ".pytest_cache")


def is_preserved(relative: Path) -> bool:
    parts = tuple(part for part in Path(str(relative).replace("\\", "/")).parts if part not in ("", "."))
    for keep in PRESERVE:
        keep_parts = tuple(Path(keep).parts)
        if parts[: len(keep_parts)] == keep_parts:
            return True
    return False


def is_deletable(relative: Path) -> bool:
    parts = tuple(part for part in Path(str(relative).replace("\\", "/")).parts if part not in ("", "."))
    for drop in DELETABLE:
        keep_parts = tuple(Path(drop).parts)
        if parts[: len(keep_parts)] == keep_parts:
            return True
    return False


class AppMutex:
    """命名互斥锁；仅用于存在性判断与独占标记。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._handle = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            self._handle = object()
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return False
        already = ctypes.get_last_error() == 183
        if already:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        """显式释放锁；必须在拉起新版进程之前调用。"""
        handle, self._handle = self._handle, None
        if handle is None or sys.platform != "win32":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle(handle)

    @staticmethod
    def exists(name: str) -> bool:
        if sys.platform != "win32":
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenMutexW.restype = ctypes.c_void_p
        kernel32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenMutexW(0x00100000, False, name)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", int(port))) == 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class Progress:
    """Tkinter 更新窗口；不可用时降级为终端字符进度。"""

    def __init__(self, use_window: bool = True) -> None:
        self.root = None
        self.bar = None
        self.label = None
        self.window_ok = False
        if use_window:
            try:
                import tkinter as tk
                from tkinter import ttk

                self.root = tk.Tk()
                self.root.title("优学院助手正在更新")
                self.root.resizable(False, False)
                self.root.attributes("-topmost", True)
                tk.Label(self.root, text="优学院助手正在更新", font=("Microsoft YaHei UI", 12, "bold")).pack(padx=28, pady=(18, 6))
                self.label = tk.Label(self.root, text="正在准备……", font=("Microsoft YaHei UI", 10))
                self.label.pack(padx=28, pady=2)
                self.bar = ttk.Progressbar(self.root, length=320, mode="determinate", maximum=100)
                self.bar.pack(padx=28, pady=10)
                tk.Label(self.root, text="请勿关闭此窗口", font=("Microsoft YaHei UI", 9), fg="#7b8495").pack(padx=28, pady=(0, 16))
                self.root.protocol("WM_DELETE_WINDOW", lambda: None)
                self.root.update()
                self.window_ok = True
            except Exception:  # noqa: BLE001 - 无桌面环境时降级
                self.window_ok = False
                self.root = None
        self._last_percent = -1

    def update(self, percent: int, text: str) -> None:
        percent = max(0, min(100, int(percent)))
        if self.window_ok and self.root is not None:
            self.label.config(text=text)
            self.bar["value"] = percent
            self.root.update_idletasks()
            self.root.update()
        elif percent != self._last_percent:
            filled = "#" * (percent // 4)
            print(f"[{filled:<25}] {percent:3d}% {text}", flush=True)
            self._last_percent = percent

    def message(self, text: str) -> None:
        if self.window_ok and self.root is not None:
            self.label.config(text=text)
            self.root.update()
        print(text, flush=True)

    def close(self) -> None:
        if self.window_ok and self.root is not None:
            try:
                self.root.destroy()
            except Exception:  # noqa: BLE001
                pass


def iter_files(base: Path) -> list[Path]:
    result: list[Path] = []
    for current, _dirs, names in os.walk(base):
        for name in names:
            result.append(Path(current) / name)
    return result


def safe_target(dest: Path, arc_name: str) -> Path:
    name = arc_name.replace("\\", "/")
    if len(name) >= 2 and name[1] == ":" or name.startswith("/"):
        raise ValueError(f"ZIP 绝对路径：{arc_name}")
    parts = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"ZIP 路径穿越：{arc_name}")
        parts.append(part)
    if not parts:
        raise ValueError(f"ZIP 空路径：{arc_name}")
    target = dest.joinpath(*parts).resolve()
    if not str(target).startswith(str(dest.resolve())):
        raise ValueError(f"ZIP 条目逃出目标目录：{arc_name}")
    return target


def strip_common_root(names: list[str]) -> tuple[bool, str]:
    tops = {name.replace("\\", "/").split("/", 1)[0] for name in names}
    if len(tops) == 1 and all("/" in name.replace("\\", "/") for name in names):
        root = tops.pop()
        if root not in ("..", "."):
            return True, root
    return False, ""


def assert_no_traversal(names: list[str]) -> None:
    for name in names:
        normalized = name.replace("\\", "/")
        if len(normalized) >= 2 and normalized[1] == ":" or normalized.startswith("/"):
            raise ValueError(f"ZIP 绝对路径：{name}")
        if ".." in normalized.split("/"):
            raise ValueError(f"ZIP 路径穿越：{name}")


def default_workdir() -> Path:
    """更新器自己的工作目录。

    冻结模式下更新器 EXE 由主程序复制到临时目录运行（主进程不假设系统有
    python.exe/pythonw.exe），工作目录就是该 EXE 所在的临时目录；
    开发模式下沿用脚本副本所在的临时目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class Installer:
    def __init__(self, payload: dict, progress: Progress) -> None:
        self.payload = payload
        self.progress = progress
        self.install_dir = Path(payload["installDir"]).resolve()
        self.zip_path = Path(payload["zip"])
        self.expected_sha = str(payload.get("sha256", "")).lower()
        self.expected_version = str(payload.get("expectedVersion", ""))
        self.from_version = str(payload.get("fromVersion", ""))
        self.ready_file = Path(payload["readyFile"])
        self.result_path = self.install_dir / "update-result.json"
        self.failures_path = self.install_dir / "update_failures.json"
        self.log_path = Path(payload.get("installerLog") or (self.install_dir / ".update" / "updater.log"))
        self.workdir = default_workdir()
        self.stage = self.workdir / "staging"
        self.backup = self.workdir / "backup"
        self.backup_manifest: dict[str, list[str]] = {"files": []}

    def log(self, message: str) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
        except OSError:
            pass

    def wait_for_app_exit(self) -> None:
        app_mutex = str(self.payload.get("appMutex") or APP_MUTEX_DEFAULT)
        ports = [int(port) for port in self.payload.get("ports", [])]
        deadline = time.monotonic() + WAIT_APP_EXIT_TIMEOUT
        while time.monotonic() < deadline:
            busy = AppMutex.exists(app_mutex) or any(port_in_use(port) for port in ports)
            if not busy:
                return
            self.progress.update(4, "正在等待助手程序退出……")
            time.sleep(0.5)
        raise RuntimeError("等待助手程序退出超时，已放弃本次安装")

    def check_package(self) -> None:
        self.progress.update(12, "正在检查更新包……")
        if not self.zip_path.is_file():
            raise RuntimeError("找不到已下载的更新包")
        actual = sha256_file(self.zip_path)
        if self.expected_sha and actual != self.expected_sha:
            raise RuntimeError("更新包 SHA-256 校验失败")
        with zipfile.ZipFile(self.zip_path) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise RuntimeError(f"更新包内文件损坏：{bad}")

    def extract_package(self) -> None:
        self.progress.update(42, "正在解压更新文件……")
        shutil.rmtree(self.stage, ignore_errors=True)
        self.stage.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.zip_path) as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
            assert_no_traversal(names)
            strip, root = strip_common_root(names)
            total = max(1, len(names))
            for index, raw_name in enumerate(names):
                name = raw_name.replace("\\", "/").split("/", 1)[1] if strip else raw_name.replace("\\", "/")
                target = safe_target(self.stage, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(raw_name) as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output)
                percent = 42 + int(18 * (index + 1) / total)
                self.progress.update(percent, f"正在解压更新文件…… {index + 1}/{total}")
        if strip:
            self.log(f"已剥离压缩包公共顶层目录 {root}")

    def backup_current(self) -> None:
        self.progress.update(22, "正在备份当前版本……")
        shutil.rmtree(self.backup, ignore_errors=True)
        self.backup.mkdir(parents=True, exist_ok=True)
        files = [path for path in iter_files(self.install_dir)
                 if not self._skip(path)
                 and not is_deletable(path.relative_to(self.install_dir))]
        total = max(1, len(files))
        for index, path in enumerate(files):
            relative = path.relative_to(self.install_dir)
            target = self.backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            self.backup_manifest["files"].append(str(relative).replace("\\", "/"))
            if index % 20 == 0:
                self.progress.update(22 + int(18 * (index + 1) / total), f"正在备份当前版本…… {index + 1}/{total}")
        (self.backup / "backup-manifest.json").write_text(json.dumps(self.backup_manifest, ensure_ascii=False), encoding="utf-8")
        self.progress.update(40, "备份完成")

    def _skip(self, path: Path) -> bool:
        """保留用户数据；同时绝不触碰更新器自己的工作目录。"""
        relative = path.relative_to(self.install_dir)
        if is_preserved(relative):
            return True
        return self._in_workdir(path)

    def _in_workdir(self, path: Path) -> bool:
        """按文件系统身份判断是否位于更新器工作目录中。"""
        # Windows runner 的临时目录可能同时以长路径、8.3 短路径或目录联接出现；
        # 单纯比较字符串会把同一个目录误判成两个位置。
        for candidate in (path, *path.parents):
            try:
                if os.path.samefile(candidate, self.workdir):
                    return True
            except OSError:
                continue
        candidate = os.path.normcase(os.path.abspath(os.fspath(path)))
        workdir = os.path.normcase(os.path.abspath(os.fspath(self.workdir)))
        try:
            return os.path.commonpath((candidate, workdir)) == workdir
        except ValueError:
            return False

    def replace_files(self) -> None:
        self.progress.update(62, "正在替换程序文件……")
        # 先删除将被替换的旧文件，再移入新版文件；用户数据目录全程不触碰。
        for path in iter_files(self.install_dir):
            if self._skip(path):
                continue
            if path.is_file():
                path.unlink()
        for current, dirs, _names in os.walk(self.install_dir, topdown=False):
            relative = Path(current).relative_to(self.install_dir)
            if is_preserved(relative):
                dirs[:] = []
                continue
            for name in dirs:
                candidate = Path(current) / name
                if self._in_workdir(candidate):
                    continue
                rel = candidate.relative_to(self.install_dir)
                if not is_preserved(rel):
                    shutil.rmtree(candidate, ignore_errors=True)
        staged_files = [path for path in iter_files(self.stage)]
        total = max(1, len(staged_files))
        for index, path in enumerate(staged_files):
            relative = path.relative_to(self.stage)
            if is_preserved(relative):
                continue
            target = self.install_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            percent = 62 + int(18 * (index + 1) / total)
            self.progress.update(percent, f"正在替换程序文件…… {index + 1}/{total}")

    def verify_new_version(self) -> None:
        self.progress.update(82, "正在校验新版本……")
        version_file = self._first_existing((self.install_dir / "version.py", self.install_dir / "_internal" / "version.py"))
        if version_file is None:
            raise RuntimeError("新版本缺少版本信息文件 version.py")
        if self.expected_version and self.expected_version not in version_file.read_text(encoding="utf-8"):
            raise RuntimeError("新版本 version.py 与预期版本不一致")
        dist_index = self._first_existing((
            self.install_dir / "web" / "dist" / "index.html",
            self.install_dir / "_internal" / "web" / "dist" / "index.html",
        ))
        if dist_index is None:
            raise RuntimeError("新版本缺少前端构建产物 web/dist")

    @staticmethod
    def _first_existing(candidates: tuple[Path, ...]) -> Path | None:
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def cleanup(self) -> None:
        self.progress.update(92, "正在清理临时文件……")
        shutil.rmtree(self.stage, ignore_errors=True)
        try:
            self.zip_path.unlink(missing_ok=True)
            part = self.zip_path.with_suffix(".zip.part")
            part.unlink(missing_ok=True)
        except OSError:
            pass
        shutil.rmtree(self.workdir / "backup", ignore_errors=True)

    def restart(self) -> None:
        self.progress.update(97, "正在重新启动助手……")
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
        if getattr(sys, "frozen", False):
            # 冻结发行版没有 Python 解释器，直接重新拉起主程序 EXE。
            subprocess.Popen(
                [str(self.install_dir / APP_EXECUTABLE)],
                cwd=str(self.install_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
            )
            return
        python = Path(sys.base_prefix) / "pythonw.exe" if sys.platform == "win32" else Path(sys.executable)
        if not python.is_file():
            python = Path(sys.executable)
        subprocess.Popen(
            [str(python), str(self.install_dir / "browser_launcher.py")],
            cwd=str(self.install_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )

    def write_result(self, result: str, *, stage: str = "", error: str = "", rolled_back: bool = False) -> None:
        failures = 0
        try:
            failures = int(json.loads(self.failures_path.read_text(encoding="utf-8")).get("count", 0))
        except (OSError, ValueError):
            failures = 0
        if result == "success":
            failures = 0
        elif result == "failed":
            failures += 1
        try:
            self.failures_path.write_text(json.dumps({"count": failures, "lastAt": self._now()}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        advice = ""
        if result == "failed":
            if failures >= 3:
                advice = "已连续失败 3 次，自动重试已停止；请查看日志或手动处理。"
            elif failures == 2:
                advice = "已失败 2 次，建议重新下载完整更新包后重试。"
            else:
                advice = "可以稍后重试安装。"
        payload = {
            "result": result,
            "from": self.from_version,
            "to": self.expected_version,
            "stage": stage,
            "error": error,
            "rolledBack": rolled_back,
            "advice": advice,
            "failures": failures,
            "acknowledged": result == "success",
            "time": self._now(),
        }
        try:
            self.result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            self.log("写入 update-result.json 失败")

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def rollback(self) -> None:
        """从备份恢复旧版本；同时移除替换阶段新写入的文件。"""
        self.progress.update(70, "正在恢复旧版本……")
        manifest_path = self.backup / "backup-manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError("找不到备份清单，无法自动恢复")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        restored: set[str] = set()
        for relative in manifest["files"]:
            source = self.backup / relative
            target = self.install_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored.add(str(relative))
        for path in iter_files(self.install_dir):
            if self._skip(path):
                continue
            relative = path.relative_to(self.install_dir)
            text = str(relative).replace("\\", "/")
            if is_deletable(relative) or text in restored:
                continue
            if path.is_file():
                path.unlink()

    def run(self) -> int:
        self.progress.update(2, "正在取得更新锁……")
        lock = AppMutex(str(self.payload.get("updatingMutex") or UPDATING_MUTEX_DEFAULT))
        if not lock.acquire():
            self.log("更新锁已被占用，放弃本次安装")
            return 2
        try:
            self.ready_file.parent.mkdir(parents=True, exist_ok=True)
            self.ready_file.write_text(json.dumps({"ok": True, "pid": os.getpid(), "time": self._now()}), encoding="utf-8")
            # 等待阶段没有修改任何文件：超时只放弃本次安装，不写失败记录，也不重启。
            try:
                self.wait_for_app_exit()
            except Exception as error:  # noqa: BLE001
                self.log(f"等待程序退出失败：{error}")
                self.progress.message("等待助手程序退出超时，已放弃本次安装。")
                time.sleep(2)
                return 1
            stage = "检查更新包"
            installation_changed = False
            try:
                self.check_package()
                stage = "创建备份"
                self.backup_current()
                stage = "解压文件"
                self.extract_package()
                stage = "替换文件"
                # replace_files 从第一步开始就可能删除旧文件，因此调用前即视为已修改。
                installation_changed = True
                self.replace_files()
                stage = "校验新版本"
                self.verify_new_version()
            except Exception as error:  # noqa: BLE001
                if not installation_changed:
                    self.log(f"安装失败（{stage}）：{error}；尚未修改程序文件，无需回滚")
                    self.write_result("failed", stage=stage, error=str(error), rolled_back=True)
                    self.progress.message("更新未完成，原版本未被修改。")
                    lock.release()
                    try:
                        self.restart()
                    except OSError as restart_error:
                        self.log(f"重新启动原版本失败：{restart_error}")
                        self.progress.message("原版本仍可使用，但自动重启失败，请手动启动助手。")
                    time.sleep(1.5)
                    return 1
                self.log(f"安装失败（{stage}）：{error}；开始回滚")
                try:
                    stage = "回滚"
                    self.rollback()
                    self.write_result("failed", stage=stage, error=str(error), rolled_back=True)
                    self.progress.message("更新未完成，已恢复到旧版本。")
                    lock.release()
                    self.restart()
                    self.progress.update(100, "已恢复旧版本，正在重新启动……")
                    time.sleep(1.5)
                    return 1
                except Exception as rollback_error:  # noqa: BLE001
                    self.log(f"回滚也失败：{rollback_error}")
                    self.write_result("failed", stage=stage, error=f"{error}；回滚失败：{rollback_error}", rolled_back=False)
                    self.recovery_window(str(error), str(rollback_error))
                    return 3
            self.write_result("success")
            try:
                self.cleanup()
                lock.release()
                self.restart()
            except OSError as error:
                self.log(f"清理或重启失败：{error}")
                self.progress.message("更新完成，但自动重启失败，请手动启动助手。")
                time.sleep(2)
                return 0
            self.progress.update(100, "更新完成，正在重新启动……")
            time.sleep(1.5)
            return 0
        except Exception as error:  # noqa: BLE001
            self.log(f"更新中止：{error}")
            self.write_result("failed", stage="准备", error=str(error), rolled_back=True)
            self.progress.message(f"更新未完成：{error}")
            time.sleep(2)
            return 1
        finally:
            lock.release()
            self.progress.close()

    def recovery_window(self, install_error: str, rollback_error: str) -> None:
        """安装与回滚都失败：保持窗口打开，提供手动恢复入口，不自动重启。"""
        self.progress.message("更新未完成，自动恢复失败。")
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk

            root = tk.Tk()
            root.title("更新未完成")
            root.attributes("-topmost", True)
            root.resizable(False, False)
            tk.Label(root, text="更新未完成", font=("Microsoft YaHei UI", 12, "bold")).pack(padx=24, pady=(16, 4))
            tk.Label(root, text="安装与自动恢复均失败，程序文件可能不完整。", wraplength=360).pack(padx=24)
            tk.Label(root, text=f"安装错误：{install_error}", wraplength=360, fg="#a33").pack(padx=24, pady=(6, 0))
            tk.Label(root, text=f"回滚错误：{rollback_error}", wraplength=360, fg="#a33").pack(padx=24)

            def retry() -> None:
                try:
                    self.rollback()
                    messagebox.showinfo("更新未完成", "恢复完成，即将重新启动助手。")
                    root.destroy()
                    self.restart()
                except Exception as error:  # noqa: BLE001
                    messagebox.showerror("更新未完成", f"恢复失败：{error}")

            def open_backup() -> None:
                self.backup.mkdir(parents=True, exist_ok=True)
                os.startfile(str(self.backup))  # noqa: S606

            def open_log() -> None:
                os.startfile(str(self.log_path))  # noqa: S606

            buttons = ttk.Frame(root)
            buttons.pack(padx=24, pady=14)
            ttk.Button(buttons, text="再次恢复", command=retry).pack(side="left", padx=4)
            ttk.Button(buttons, text="打开备份目录", command=open_backup).pack(side="left", padx=4)
            ttk.Button(buttons, text="查看日志", command=open_log).pack(side="left", padx=4)
            ttk.Button(buttons, text="退出", command=root.destroy).pack(side="left", padx=4)
            root.mainloop()
        except Exception:  # noqa: BLE001
            print("安装与回滚均失败，请查看日志并手动恢复。", flush=True)
            print(f"备份目录：{self.backup}", flush=True)
            print(f"日志：{self.log_path}", flush=True)


def main(argv: list[str]) -> int:
    if "--payload" not in argv:
        print("用法：updater_installer.py --payload payload.json")
        return 2
    payload_path = Path(argv[argv.index("--payload") + 1])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    # 安装进度只在浏览器消息窗口中呈现，不再额外弹出原生进度窗口。
    progress = Progress(use_window=False)
    installer = Installer(payload, progress)
    return installer.run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
