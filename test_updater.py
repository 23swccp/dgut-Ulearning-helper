"""自动更新相关的不依赖网络与真实安装的回归测试。运行：python -m unittest -v test_updater.py"""

import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import updater_installer
import yxy_mutex
from version import normalize_github_repository
from yxy_updater import (
    UpdateError, UpdateManager, compare_versions, download_to_file, extract_zip_safely,
    is_preserved, parse_manifest, select_targets_to_close, sha256_file, close_assistant_tabs,
)


def make_zip_bytes() -> tuple[bytes, str]:
    """构造一个合法的更新包字节串与其 SHA-256。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("version.py", 'APP_VERSION = "0.3.0"')
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def make_manifest(version: str, sha: str, size: int = 10, changelog: str = "修复若干问题。") -> dict:
    return {
        "version": version,
        "url": f"https://example.com/yxy-{version}.zip",
        "sha256": sha,
        "size": size,
        "changelog": changelog,
        "publishedAt": "2026-08-29T10:00:00+08:00",
    }


class FakeTransport:
    """测试用网络层：可编程的检查结果与下载行为。"""

    def __init__(self, release: dict | None = None, content: bytes = b"", fail_times: int = 0) -> None:
        self.release = release or {}
        self.content = content
        self.fail_times = fail_times
        self.download_calls = 0

    def get_json(self, url: str, headers: dict | None = None):
        if "releases" in url:
            return self.release
        return self.release.get("_manifest")

    def download(self, url, dest, *, expected_size=0, progress=None, stop=None):
        self.download_calls += 1
        if self.download_calls <= self.fail_times:
            raise UpdateError("网络连接中断")
        mode = "ab" if dest.is_file() else "wb"
        with open(dest, mode) as handle:
            handle.write(self.content)
        if progress:
            progress(len(self.content), len(self.content))


class VersionTests(unittest.TestCase):
    def test_version_comparison(self):
        self.assertEqual(compare_versions("v0.2.0", "0.2.0"), 0)
        self.assertLess(compare_versions("v0.2.9", "v0.3.0"), 0)
        self.assertGreater(compare_versions("v0.10.0", "v0.9.9"), 0)
        self.assertEqual(compare_versions("v0.3.0", "0.3"), 0)
        self.assertGreater(compare_versions("v1.0.0-rc.1", "v0.9.0"), 0)

    def test_repository_source_accepts_common_github_formats(self):
        self.assertEqual(normalize_github_repository("owner/new-name"), "owner/new-name")
        self.assertEqual(normalize_github_repository("https://github.com/owner/new-name.git"), "owner/new-name")
        self.assertEqual(normalize_github_repository("git@github.com:owner/new-name.git"), "owner/new-name")
        self.assertEqual(normalize_github_repository("https://example.com/owner/repo"), "")


class ManifestTests(unittest.TestCase):
    def test_parse_manifest_fields(self):
        manifest = parse_manifest(make_manifest("0.3.0", "a" * 64))
        self.assertEqual(manifest["version"], "0.3.0")
        self.assertEqual(manifest["changelog"], "修复若干问题。")

    def test_parse_manifest_rejects_invalid(self):
        with self.assertRaises(UpdateError):
            parse_manifest({"version": "0.3.0", "url": ""})
        with self.assertRaises(UpdateError):
            parse_manifest(make_manifest("0.3.0", "short"))
        with self.assertRaises(UpdateError):
            parse_manifest({"url": "https://x", "sha256": "a" * 64})


class DownloadTests(unittest.TestCase):
    def test_sha256_of_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.bin"
            path.write_bytes(b"hello")
            self.assertEqual(sha256_file(path), hashlib.sha256(b"hello").hexdigest())

    def test_download_checks_size_and_leaves_part_file(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "pkg.part"
            with patch("yxy_updater.urlopen") as fake:
                response = fake.return_value
                response.status = 200
                response.headers = {"Content-Length": "3"}
                response.read.side_effect = [b"12345", b""]
                with self.assertRaises(UpdateError):
                    download_to_file("https://example.com/p.zip", dest, expected_size=0)
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), b"12345")


class ZipSafetyTests(unittest.TestCase):
    def make_zip(self, directory: Path, entries: dict[str, bytes], name: str = "pkg.zip") -> Path:
        path = Path(directory) / name
        with zipfile.ZipFile(path, "w") as archive:
            for arc, data in entries.items():
                archive.writestr(arc, data)
        return path

    def test_zip_slip_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evil = self.make_zip(root, {"ok.txt": b"fine", "../evil.txt": b"bad"})
            with self.assertRaises(UpdateError):
                extract_zip_safely(evil, root / "out")
            self.assertFalse((root.parent / "evil.txt").exists())

    def test_absolute_paths_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evil = self.make_zip(root, {r"C:\Windows\system32\evil.txt": b"bad"})
            with self.assertRaises(UpdateError):
                extract_zip_safely(evil, root / "out")

    def test_common_root_is_stripped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.make_zip(root, {"yxy-assistant-v0.3.0/version.py": b"APP_VERSION = '0.3.0'"})
            extracted = extract_zip_safely(package, root / "out")
            self.assertEqual([path.name for path in extracted], ["version.py"])
            self.assertTrue((root / "out" / "version.py").is_file())


class PreserveTests(unittest.TestCase):
    def test_user_data_is_preserved(self):
        for name in ("config.json", "auth.json", "account.json", "签到记录.md", "browser_profile/Default/Cookies",
                     "browser_profile", ".update/state.json", "update-result.json"):
            self.assertTrue(is_preserved(name), name)
        self.assertFalse(is_preserved("yxy_backend.py"))
        self.assertFalse(is_preserved("web/dist/index.html"))

    def test_installer_preserve_list_matches(self):
        self.assertTrue(updater_installer.is_preserved("config.json"))
        self.assertTrue(updater_installer.is_preserved("browser_profile/x/y"))
        self.assertFalse(updater_installer.is_preserved("yxy_backend.py"))


class MutexTests(unittest.TestCase):
    def test_named_mutex_blocks_second_acquire(self):
        backend = yxy_mutex.InProcessMutexBackend()
        yxy_mutex.set_backend(backend)
        try:
            first = yxy_mutex.NamedMutex(yxy_mutex.APP_MUTEX)
            second = yxy_mutex.NamedMutex(yxy_mutex.APP_MUTEX)
            self.assertTrue(first.try_acquire())
            self.assertFalse(second.try_acquire())
            self.assertTrue(yxy_mutex.app_mutex_exists())
            first.release()
            self.assertFalse(yxy_mutex.app_mutex_exists())
            self.assertTrue(second.try_acquire())
            second.release()
        finally:
            yxy_mutex.set_backend(yxy_mutex.default_backend())


class TabCloseTests(unittest.TestCase):
    TARGETS = [
        {"type": "page", "url": "http://127.0.0.1:1420/", "id": "assistant"},
        {"type": "page", "url": "http://127.0.0.1:1420/settings", "id": "assistant2"},
        {"type": "page", "url": "https://ua.dgut.edu.cn/login", "id": "yxy-login"},
        {"type": "page", "url": "https://lms.dgut.edu.cn/course/9", "id": "yxy-course"},
        {"type": "page", "url": "http://localhost:1420/", "id": "localhost"},
        {"type": "page", "url": "http://127.0.0.1:3000/other", "id": "other-port"},
        {"type": "iframe", "url": "http://127.0.0.1:1420/embed", "id": "iframe"},
        {"type": "background_page", "url": "http://127.0.0.1:1420/bg", "id": "bg"},
    ]

    def test_only_matching_assistant_tabs_are_selected(self):
        selected = select_targets_to_close(self.TARGETS, "http://127.0.0.1:1420")
        self.assertEqual([item["id"] for item in selected], ["assistant", "assistant2"])

    def test_selected_tabs_close_via_cdp(self):
        closed = []
        sent = []

        class FakeSocket:
            def send(self, data: str) -> None:
                sent.append(json.loads(data))

            def recv(self) -> str:
                return json.dumps({"id": len(sent), "result": {"success": True}})

            def close(self) -> None:
                closed.append(True)

        count = close_assistant_tabs(
            9222, "http://127.0.0.1:1420",
            list_targets=lambda _port: self.TARGETS,
            browser_socket=lambda _port: FakeSocket(),
        )
        self.assertEqual(count, 2)
        self.assertEqual([message["method"] for message in sent], ["Target.closeTarget", "Target.closeTarget"])
        self.assertEqual([message["params"]["targetId"] for message in sent], ["assistant", "assistant2"])


class UpdateManagerTests(unittest.TestCase):
    def make_manager(self, root: Path, version: str = "0.2.0", transport=None, sleep=None) -> UpdateManager:
        manager = UpdateManager(
            root,
            version=version,
            release_api="https://api.example.com/releases/latest",
            transport=transport,
            emit_event=lambda *args, **kwargs: {},
            sleep=sleep or (lambda _seconds: None),
        )
        return manager

    def sha_for(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def test_check_finds_new_version_and_downloads(self):
        content, sha = make_zip_bytes()
        manifest = make_manifest("0.3.0", sha, size=len(content))
        transport = FakeTransport(release={"assets": [{"name": "manifest.json", "browser_download_url": "m"}], "_manifest": manifest}, content=content)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            manager.transport = transport
            result = manager.check()
            self.assertTrue(result["updateAvailable"])
            manager._download_thread.join(5)
            snapshot = manager.snapshot()
            self.assertEqual(snapshot["state"], "ready_to_install")
            self.assertEqual(snapshot["percent"], 100)
            self.assertTrue(manager.zip_path.is_file())

    def test_check_reports_up_to_date(self):
        manifest = make_manifest("0.2.0", "a" * 64)
        transport = FakeTransport(release={"assets": [{"name": "manifest.json", "browser_download_url": "m"}], "_manifest": manifest})
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            manager.transport = transport
            result = manager.check()
            self.assertFalse(result["updateAvailable"])
            self.assertEqual(manager.snapshot()["state"], "idle")

    def test_main_process_can_exit_only_after_installer_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            stopped = []

            manager._set_state("handoff")
            self.assertTrue(manager.snapshot()["handoff"])
            self.assertFalse(manager.snapshot()["readyForExit"])
            result = manager.shutdown_for_update(lambda: stopped.append(True))
            self.assertFalse(result["ok"])
            self.assertEqual(stopped, [])

            manager._set_state("waiting_for_exit")
            self.assertTrue(manager.snapshot()["readyForExit"])
            with patch("yxy_updater.close_assistant_tabs", return_value=0):
                result = manager.shutdown_for_update(lambda: stopped.append(True))
            self.assertTrue(result["ok"])
            self.assertEqual(stopped, [True])

    def test_download_failure_after_retries_records_message(self):
        content = b"fake-zip"
        manifest = make_manifest("0.3.0", self.sha_for(content))
        transport = FakeTransport(release={"assets": [{"name": "manifest.json", "browser_download_url": "m"}], "_manifest": manifest}, content=content, fail_times=99)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            manager.transport = transport
            manager.check()
            manager._download_thread.join(10)
            snapshot = manager.snapshot()
            self.assertEqual(snapshot["state"], "download_failed")
            self.assertEqual(snapshot["unreadCount"], 2)  # 发现新版本 + 下载失败
            self.assertIn("下载失败", snapshot["messages"][0]["title"])
            self.assertTrue(snapshot["canRetryDownload"])
            self.assertEqual(transport.download_calls, 3)

    def test_sha_mismatch_fails_download(self):
        content = b"corrupted"
        manifest = make_manifest("0.3.0", self.sha_for(b"expected"))
        transport = FakeTransport(release={"assets": [{"name": "manifest.json", "browser_download_url": "m"}], "_manifest": manifest}, content=content)
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            manager.transport = transport
            manager.check()
            manager._download_thread.join(5)
            self.assertEqual(manager.snapshot()["state"], "download_failed")
            self.assertFalse(manager.zip_path.is_file())

    def test_restore_resumes_interrupted_download(self):
        content, sha = make_zip_bytes()
        manifest = make_manifest("0.3.0", sha, size=len(content))
        transport = FakeTransport(release={"assets": [{"name": "manifest.json", "browser_download_url": "m"}], "_manifest": manifest}, content=content)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            manager.transport = transport
            manager._mutate(
                latestVersion="0.3.0", downloadUrl=manifest["url"], sha256=manifest["sha256"],
                size=manifest["size"], changelog=manifest["changelog"],
            )
            manager._set_state("downloading")
            manager._persist(force=True)
            (root / ".update").mkdir(parents=True, exist_ok=True)
            (root / ".update" / "package.zip.part").write_bytes(b"")
            restarted = UpdateManager(root, version="0.2.0", release_api="https://api.example.com/releases/latest",
                                      transport=transport, emit_event=lambda *a, **k: {}, sleep=lambda s: None)
            restarted.restore()
            restarted._download_thread.join(5)
            self.assertEqual(restarted.snapshot()["state"], "ready_to_install")

    def test_restore_ready_to_install_keeps_package_after_recheck(self):
        content, sha = make_zip_bytes()
        manifest = make_manifest("0.3.0", sha, size=len(content))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            (root / ".update").mkdir(parents=True, exist_ok=True)
            (root / ".update" / "package.zip").write_bytes(content)
            manager._mutate(
                latestVersion="0.3.0", downloadUrl=manifest["url"], sha256=manifest["sha256"],
                size=len(content), changelog=manifest["changelog"], total=len(content), downloaded=len(content),
            )
            manager._set_state("ready_to_install")
            manager._persist(force=True)
            restarted = self.make_manager(root)
            restarted.restore()
            self.assertEqual(restarted.snapshot()["state"], "ready_to_install")
            # 启动后的自动检查不得丢弃待安装包
            transport = FakeTransport(release={"assets": [{"name": "manifest.json", "browser_download_url": "m"}], "_manifest": manifest})
            restarted.transport = transport
            result = restarted.check()
            self.assertTrue(result["updateAvailable"])
            self.assertEqual(restarted.snapshot()["state"], "ready_to_install")

    def test_postpone_keeps_ready_state(self):
        content, sha = make_zip_bytes()
        manifest = make_manifest("0.3.0", sha, size=len(content))
        transport = FakeTransport(release={"assets": [{"name": "manifest.json", "browser_download_url": "m"}], "_manifest": manifest}, content=content)
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(Path(directory))
            manager.transport = transport
            manager.check()
            manager._download_thread.join(5)
            # 暂不更新 = 不调用 install：状态与文件都保持待安装
            snapshot = manager.snapshot()
            self.assertTrue(snapshot["canInstall"])
            self.assertEqual(snapshot["state"], "ready_to_install")
            restarted = self.make_manager(Path(directory))
            restarted.restore()
            self.assertEqual(restarted.snapshot()["state"], "ready_to_install")

    def test_failure_dialog_shown_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            (root / "update-result.json").write_text(json.dumps({
                "result": "failed", "from": "0.2.0", "to": "0.3.0", "stage": "install",
                "error": "boom", "rolledBack": True, "acknowledged": False,
            }), encoding="utf-8")
            dialog = manager.pending_failure_dialog()
            self.assertIsNotNone(dialog)
            self.assertEqual(dialog["failedVersion"], "0.3.0")
            manager.ack_failure()
            self.assertIsNone(manager.pending_failure_dialog())

    def test_failure_dialog_hidden_for_recovery_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            (root / "update-result.json").write_text(json.dumps({
                "result": "failed", "rolledBack": False, "acknowledged": False,
            }), encoding="utf-8")
            self.assertIsNone(manager.pending_failure_dialog())

    def test_page_refresh_recovers_downloading_state(self):
        # 模拟：状态持久化为 downloading，刷新页面后 snapshot 仍能给出真实进度
        content, sha = make_zip_bytes()
        manifest = make_manifest("0.3.0", sha, size=len(content))
        transport = FakeTransport(release={"assets": [{"name": "manifest.json", "browser_download_url": "m"}], "_manifest": manifest}, content=content)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            manager.transport = transport
            manager._mutate(latestVersion="0.3.0", downloadUrl=manifest["url"], sha256=sha, size=len(content))
            manager._set_state("downloading")
            manager._mutate(downloaded=42, total=len(content))
            manager._persist(force=True)
            restarted = self.make_manager(root, transport=transport)
            restarted.restore()
            restarted._download_thread.join(5)
            self.assertEqual(restarted.snapshot()["state"], "ready_to_install")

    def test_state_json_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            manager._set_state("download_failed")
            manager._mutate(latestVersion="0.3.0", error="网络连接中断")
            manager._persist(force=True)
            raw = json.loads((root / ".update" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["state"], "download_failed")
            self.assertEqual(raw["latestVersion"], "0.3.0")

    def test_check_failure_keeps_ready_to_install_package(self):
        content, sha = make_zip_bytes()
        manifest = make_manifest("0.3.0", sha, size=len(content))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            (root / ".update").mkdir(parents=True, exist_ok=True)
            (root / ".update" / "package.zip").write_bytes(content)
            manager._mutate(
                latestVersion="0.3.0", downloadUrl=manifest["url"], sha256=manifest["sha256"],
                size=len(content), changelog=manifest["changelog"], total=len(content), downloaded=len(content),
            )
            manager._set_state("ready_to_install")
            manager._persist(force=True)
            # 网络故障：检查更新抛错时不得丢弃待安装状态
            with patch.object(manager, "release_api", ""):
                result = manager.check()
            self.assertFalse(result["ok"])
            self.assertEqual(manager.snapshot()["state"], "ready_to_install")
            self.assertTrue(manager.snapshot()["canInstall"])

    def test_waiting_for_exit_restores_result_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            manager._set_state("waiting_for_exit")
            manager._persist(force=True)
            (root / "update-result.json").write_text(json.dumps({
                "result": "success", "from": "0.2.0", "to": "0.3.0",
                "time": "2026-08-29T12:00:00+08:00", "acknowledged": True,
            }), encoding="utf-8")

            restarted = self.make_manager(root, version="0.3.0")
            restarted.restore()
            self.assertEqual(restarted.snapshot()["state"], "completed")
            self.assertEqual(len(restarted.snapshot()["messages"]), 1)

            restarted_again = self.make_manager(root, version="0.3.0")
            restarted_again.restore()
            self.assertEqual(len(restarted_again.snapshot()["messages"]), 1)


class InstallerLogicTests(unittest.TestCase):
    """不真正运行独立更新器进程，只测试备份/替换/回滚的核心文件操作。"""

    def test_main_disables_native_progress_window(self):
        with tempfile.TemporaryDirectory() as directory:
            payload_path = Path(directory) / "payload.json"
            payload_path.write_text("{}", encoding="utf-8")
            with patch.object(updater_installer, "Progress") as progress_type, \
                    patch.object(updater_installer, "Installer") as installer_type:
                installer_type.return_value.run.return_value = 0
                self.assertEqual(updater_installer.main(["updater_installer.py", "--payload", str(payload_path)]), 0)
                progress_type.assert_called_once_with(use_window=False)

    def make_installer(self, root: Path) -> updater_installer.Installer:
        payload = {
            "installDir": str(root),
            "zip": str(root / "pkg.zip"),
            "sha256": "",
            "expectedVersion": "0.3.0",
            "fromVersion": "0.2.0",
            "readyFile": str(root / "ready.json"),
        }
        progress = updater_installer.Progress(use_window=False)
        installer = updater_installer.Installer(payload, progress)
        installer.workdir = root / "work"
        installer.workdir.mkdir(parents=True, exist_ok=True)
        installer.stage = installer.workdir / "staging"
        installer.backup = installer.workdir / "backup"
        installer.log_path = installer.workdir / "updater.log"
        return installer

    def build_old_app(self, root: Path) -> None:
        (root / "version.py").write_text('APP_VERSION = "0.2.0"', encoding="utf-8")
        (root / "yxy_backend.py").write_text("# old backend", encoding="utf-8")
        (root / "config.json").write_text("{}", encoding="utf-8")
        (root / "auth.json").write_text("{}", encoding="utf-8")
        (root / "签到记录.md").write_text("记录", encoding="utf-8")
        (root / "browser_profile" / "Default").mkdir(parents=True, exist_ok=True)
        (root / "browser_profile" / "Default" / "Cookies").write_bytes(b"cookies")
        (root / "web" / "src").mkdir(parents=True, exist_ok=True)
        (root / "web" / "src" / "App.tsx").write_text("// old", encoding="utf-8")

    def build_new_zip(self, root: Path) -> None:
        package = zipfile.ZipFile(root / "pkg.zip", "w")
        package.writestr("yxy-assistant/version.py", 'APP_VERSION = "0.3.0"')
        package.writestr("yxy-assistant/yxy_backend.py", "# new backend")
        package.writestr("yxy-assistant/web/dist/index.html", "<html>0.3.0</html>")
        package.close()

    def test_install_success_preserves_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_old_app(root)
            self.build_new_zip(root)
            installer = self.make_installer(root)
            installer.check_package()
            installer.backup_current()
            installer.extract_package()
            installer.replace_files()
            installer.verify_new_version()
            self.assertIn('APP_VERSION = "0.3.0"', (root / "version.py").read_text(encoding="utf-8"))
            self.assertTrue((root / "web" / "dist" / "index.html").is_file())
            # 用户数据原样保留
            self.assertEqual(json.loads((root / "config.json").read_text(encoding="utf-8")), {})
            self.assertTrue((root / "browser_profile" / "Default" / "Cookies").is_file())
            self.assertEqual((root / "签到记录.md").read_text(encoding="utf-8"), "记录")
            # 旧版本专属文件被移除
            self.assertFalse((root / "web" / "src" / "App.tsx").exists())

    @unittest.skipUnless(os.name == "nt", "Windows 路径大小写规则")
    def test_workdir_detection_is_case_insensitive_on_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = self.make_installer(Path(directory))
            staged = installer.workdir / "staging" / "version.py"
            differently_cased = Path(str(staged).swapcase())
            self.assertTrue(installer._in_workdir(differently_cased))

    def test_install_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_old_app(root)
            self.build_new_zip(root)
            installer = self.make_installer(root)
            installer.check_package()
            installer.backup_current()
            installer.extract_package()
            # 模拟替换中途失败：先替换一个文件再抛错
            (root / "version.py").write_text('APP_VERSION = "0.3.0"', encoding="utf-8")
            installer.rollback()
            self.assertEqual((root / "version.py").read_text(encoding="utf-8"), 'APP_VERSION = "0.2.0"')
            self.assertEqual((root / "yxy_backend.py").read_text(encoding="utf-8"), "# old backend")
            self.assertTrue((root / "config.json").is_file())

    def test_rollback_failure_leaves_recovery_required(self):
        import shutil

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_old_app(root)
            installer = self.make_installer(root)
            installer.backup_current()
            # 没有备份清单 → rollback 失败 → recovery 路径
            shutil.rmtree(installer.backup)
            with self.assertRaises(RuntimeError):
                installer.rollback()

    def test_result_records_failure_count_and_advice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = self.make_installer(root)
            installer.write_result("failed", stage="install", error="boom", rolled_back=True)
            first = json.loads((root / "update-result.json").read_text(encoding="utf-8"))
            self.assertEqual(first["failures"], 1)
            installer.write_result("failed", stage="install", error="boom", rolled_back=True)
            second = json.loads((root / "update-result.json").read_text(encoding="utf-8"))
            self.assertEqual(second["failures"], 2)
            self.assertIn("重新下载", second["advice"])
            installer.write_result("failed", stage="install", error="boom", rolled_back=True)
            third = json.loads((root / "update-result.json").read_text(encoding="utf-8"))
            self.assertEqual(third["failures"], 3)
            self.assertIn("停止", third["advice"])
            installer.write_result("success")
            success = json.loads((root / "update-result.json").read_text(encoding="utf-8"))
            self.assertEqual(success["failures"], 0)

    @patch("updater_installer.time.sleep", return_value=None)
    def test_run_releases_update_lock_before_restart(self, _sleep):
        class FakeLock:
            active = False

            def acquire(self):
                self.active = True
                return True

            def release(self):
                self.active = False

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = self.make_installer(root)
            lock = FakeLock()
            installer.wait_for_app_exit = lambda: None
            installer.check_package = lambda: None
            installer.backup_current = lambda: None
            installer.extract_package = lambda: None
            installer.replace_files = lambda: None
            installer.verify_new_version = lambda: None
            installer.cleanup = lambda: None
            installer.restart = lambda: self.assertFalse(lock.active)
            with patch("updater_installer.AppMutex", return_value=lock):
                self.assertEqual(installer.run(), 0)

    @patch("updater_installer.time.sleep", return_value=None)
    def test_preinstall_failure_does_not_attempt_rollback(self, _sleep):
        class FakeLock:
            active = False

            def acquire(self):
                self.active = True
                return True

            def release(self):
                self.active = False

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = self.make_installer(root)
            lock = FakeLock()
            installer.wait_for_app_exit = lambda: None
            installer.check_package = lambda: (_ for _ in ()).throw(RuntimeError("bad package"))
            installer.rollback = lambda: self.fail("未修改安装目录时不应回滚")
            installer.restart = lambda: self.assertFalse(lock.active)
            with patch("updater_installer.AppMutex", return_value=lock):
                self.assertEqual(installer.run(), 1)
            result = json.loads((root / "update-result.json").read_text(encoding="utf-8"))
            self.assertTrue(result["rolledBack"])
            self.assertEqual(result["stage"], "检查更新包")

    def test_installer_blocks_zip_slip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = zipfile.ZipFile(root / "pkg.zip", "w")
            package.writestr("../evil.txt", "bad")
            package.close()
            installer = self.make_installer(root)
            installer.backup_current()
            with self.assertRaises(ValueError):
                installer.extract_package()


class HandshakeTests(unittest.TestCase):
    def test_wait_for_ready_returns_payload(self):
        from yxy_updater import wait_for_ready

        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready.json"
            ready.write_text(json.dumps({"ok": True, "pid": 42}), encoding="utf-8")
            self.assertEqual(wait_for_ready(ready, timeout=1, poll=lambda _t: None)["pid"], 42)
            missing = Path(directory) / "missing.json"
            self.assertIsNone(wait_for_ready(missing, timeout=0.3, poll=lambda _t: None))


class FrozenReleaseTests(unittest.TestCase):
    """PyInstaller onedir 发行版的更新器行为。"""

    def make_installer(self, root: Path) -> updater_installer.Installer:
        payload = {
            "installDir": str(root),
            "zip": str(root / "pkg.zip"),
            "sha256": "",
            "expectedVersion": "0.3.0",
            "fromVersion": "0.2.0",
            "readyFile": str(root / "ready.json"),
        }
        installer = updater_installer.Installer(payload, updater_installer.Progress(use_window=False))
        installer.workdir = root / "work"
        installer.workdir.mkdir(parents=True, exist_ok=True)
        installer.stage = installer.workdir / "staging"
        installer.backup = installer.workdir / "backup"
        installer.log_path = installer.workdir / "updater.log"
        return installer

    def make_manager(self, root: Path) -> UpdateManager:
        return UpdateManager(
            root,
            version="0.2.0",
            release_api="https://api.example.com/releases/latest",
            emit_event=lambda *args, **kwargs: {},
            sleep=lambda _seconds: None,
        )

    def test_frozen_installer_workdir_follows_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, "frozen", True, create=True):
                payload = {
                    "installDir": directory,
                    "zip": str(Path(directory) / "pkg.zip"),
                    "readyFile": str(Path(directory) / "ready.json"),
                }
                installer = updater_installer.Installer(payload, updater_installer.Progress(use_window=False))
                self.assertEqual(installer.workdir, Path(sys.executable).resolve().parent)

    def test_frozen_verify_accepts_internal_version_and_top_level_dist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "_internal").mkdir()
            (root / "_internal" / "version.py").write_text('APP_VERSION = "0.3.0"', encoding="utf-8")
            (root / "web" / "dist").mkdir(parents=True)
            (root / "web" / "dist" / "index.html").write_text("<html>", encoding="utf-8")
            self.make_installer(root).verify_new_version()

    def test_frozen_verify_rejects_incomplete_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "_internal").mkdir()
            (root / "_internal" / "version.py").write_text('APP_VERSION = "0.3.0"', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                self.make_installer(root).verify_new_version()

    def test_frozen_restart_launches_release_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = self.make_installer(root)
            with patch.object(sys, "frozen", True, create=True), \
                    patch("updater_installer.subprocess.Popen") as popen:
                installer.restart()
            command = popen.call_args.args[0]
            self.assertEqual(Path(command[0]).name, "dgut-bot.exe")

    def test_frozen_installer_command_copies_bundled_updater_exe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / "res" / "updater" / "updater.exe"
            bundled.parent.mkdir(parents=True)
            (bundled.parent / "_internal").mkdir()
            bundled.write_bytes(b"MZ")
            (root / "run").mkdir()
            with patch.object(sys, "frozen", True, create=True), \
                    patch("yxy_updater.resource_root", return_value=root / "res"):
                command = self.make_manager(root)._installer_command(root / "run")
            self.assertEqual(Path(command[0]), root / "run" / "updater" / "updater.exe")
            self.assertTrue((root / "run" / "updater" / "updater.exe").is_file())

    def test_frozen_installer_command_requires_bundled_updater(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(sys, "frozen", True, create=True), \
                    patch("yxy_updater.resource_root", return_value=root / "empty"):
                with self.assertRaises(UpdateError):
                    self.make_manager(root)._installer_command(root / "run")


if __name__ == "__main__":
    unittest.main()
