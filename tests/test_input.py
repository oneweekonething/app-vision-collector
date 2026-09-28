"""ADB 输入层与命令错误检查单元测试（mock subprocess，无需设备）。"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collector.adb import input as adb_input  # noqa: E402
from collector.adb.connection import AdbCommandError, run_adb  # noqa: E402

LATIN_IME = "com.google.android.inputmethod.latin/.LatinIME"


def fake_run_recorder(commands: list, fail_on=()):
    """构造尊重 check=True 语义的 run_adb 假实现，记录全部调用。"""

    def run(args, device_id=None, timeout=15, binary=False, check=False):
        commands.append(list(args))
        marker = " ".join(args)
        if any(pattern in marker for pattern in fail_on):
            if check:
                raise AdbCommandError(args, 1, "命令失败")
            return subprocess.CompletedProcess(["adb"], 1, stdout="", stderr="命令失败")
        return subprocess.CompletedProcess(["adb"], 0, stdout="", stderr="")

    return run


class RunAdbCheckTest(unittest.TestCase):
    def test_nonzero_returncode_raises_when_checked(self):
        fake = subprocess.CompletedProcess(["adb"], 1, stdout="", stderr="error: device offline")
        with mock.patch("collector.adb.connection.subprocess.run", return_value=fake):
            with self.assertRaises(AdbCommandError) as ctx:
                run_adb(["shell", "input", "tap", "1", "2"], check=True)
        self.assertIn("device offline", str(ctx.exception))

    def test_binary_mode_decodes_bytes_stderr(self):
        fake = subprocess.CompletedProcess(["adb"], 1, stdout=b"", stderr=b"error: closed")
        with mock.patch("collector.adb.connection.subprocess.run", return_value=fake):
            with self.assertRaises(AdbCommandError) as ctx:
                run_adb(["shell", "true"], binary=True, check=True)
        self.assertIn("closed", str(ctx.exception))

    def test_zero_returncode_passes(self):
        fake = subprocess.CompletedProcess(["adb"], 0, stdout="OK", stderr="")
        with mock.patch("collector.adb.connection.subprocess.run", return_value=fake):
            result = run_adb(["shell", "true"], check=True)
        self.assertEqual(result.returncode, 0)

    def test_unchecked_failure_still_returns(self):
        fake = subprocess.CompletedProcess(["adb"], 1, stdout="", stderr="boom")
        with mock.patch("collector.adb.connection.subprocess.run", return_value=fake):
            result = run_adb(["shell", "dumpsys", "window"], check=False)
        self.assertEqual(result.returncode, 1)

    def test_timeout_wrapped_into_error(self):
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="adb", timeout=15)

        with mock.patch("collector.adb.connection.subprocess.run", side_effect=raise_timeout):
            with self.assertRaises(AdbCommandError) as ctx:
                run_adb(["shell", "input", "tap", "1", "2"], check=True)
        self.assertIn("超时", str(ctx.exception))


class EnsureAdbKeyboardTest(unittest.TestCase):
    def test_raises_when_keyboard_not_enabled(self):
        with mock.patch.object(adb_input, "shell", return_value=LATIN_IME):
            with self.assertRaises(AdbCommandError) as ctx:
                adb_input.ensure_adb_keyboard("TEST")
        self.assertIn("ADB Keyboard", str(ctx.exception))

    def test_switches_and_returns_previous_ime(self):
        calls = []

        def fake_shell(args, device_id=None, timeout=15, check=True):
            calls.append(args)
            if args[:2] == ["ime", "list"]:
                return f"{adb_input.ADB_IME}\n{LATIN_IME}"
            return LATIN_IME  # settings get secure default_input_method

        with mock.patch.object(adb_input, "shell", side_effect=fake_shell), \
                mock.patch.object(adb_input, "run_adb") as fake_run:
            previous = adb_input.ensure_adb_keyboard("TEST")
        self.assertEqual(previous, LATIN_IME)
        set_calls = [c for c in fake_run.call_args_list
                     if c.args[0][:3] == ["shell", "ime", "set"]]
        self.assertEqual(len(set_calls), 1)


class InputTextSafeTest(unittest.TestCase):
    def _patch_ime(self):
        def fake_shell(args, device_id=None, timeout=15, check=True):
            if args[:2] == ["ime", "list"]:
                return f"{adb_input.ADB_IME}\n{LATIN_IME}"
            return LATIN_IME
        return mock.patch.object(adb_input, "shell", side_effect=fake_shell)

    def test_switch_type_restore_sequence(self):
        commands = []
        with self._patch_ime(), \
                mock.patch.object(adb_input, "run_adb", side_effect=fake_run_recorder(commands)):
            adb_input.input_text_safe("AI通识", "TEST")

        set_calls = [c for c in commands if c[:3] == ["shell", "ime", "set"]]
        self.assertEqual(set_calls, [
            ["shell", "ime", "set", adb_input.ADB_IME],
            ["shell", "ime", "set", LATIN_IME],  # finally 恢复原输入法
        ])
        broadcast = [c for c in commands if "ADB_INPUT_B64" in c]
        self.assertEqual(len(broadcast), 1)

    def test_restore_even_when_typing_fails(self):
        commands = []
        with self._patch_ime(), mock.patch.object(
                adb_input, "run_adb",
                side_effect=fake_run_recorder(commands, fail_on=("ADB_INPUT_B64",))):
            with self.assertRaises(AdbCommandError):
                adb_input.input_text_safe("AI通识", "TEST")

        set_calls = [c for c in commands if c[:3] == ["shell", "ime", "set"]]
        self.assertEqual(len(set_calls), 2)  # 切换 + 失败后的恢复

    def test_restore_failure_after_successful_type_is_not_action_failure(self):
        # 输入已生效、仅 IME 恢复失败：不能按动作失败抛出（否则上层重试
        # 会把同一文本输入两遍），应重试恢复后降级为警告
        commands = []

        def run(args, device_id=None, timeout=15, binary=False, check=False):
            commands.append(list(args))
            marker = " ".join(args)
            # 只让最后一次恢复（目标是原输入法，非 ADB_IME）失败
            if marker.startswith("shell ime set") and adb_input.ADB_IME not in marker \
                    and commands.count(["shell", "ime", "set", LATIN_IME]) >= 1:
                if check:
                    raise AdbCommandError(args, 1, "恢复失败")
                return subprocess.CompletedProcess(["adb"], 1, stdout="", stderr="")
            return subprocess.CompletedProcess(["adb"], 0, stdout="", stderr="")

        with self._patch_ime(), \
                mock.patch.object(adb_input, "run_adb", side_effect=run), \
                mock.patch.object(adb_input.time, "sleep"):
            result = adb_input.input_text_safe("AI通识", "TEST")

        self.assertTrue(result)  # 输入已生效 → 不抛
        restores = [c for c in commands if c[:4] == ["shell", "ime", "set", LATIN_IME]]
        self.assertEqual(len(restores), 2)  # 恢复重试了一次


if __name__ == "__main__":
    unittest.main()
