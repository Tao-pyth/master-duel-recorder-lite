from __future__ import annotations

from contextlib import contextmanager
import ctypes
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from master_duel_recorder_lite import video_file_usage as usage


class FakeRestartManager:
    def __init__(self, *, start=0, register=0, end=0, replies=None):
        self.start = start
        self.register = register
        self.end = end
        self.replies = list(replies if replies is not None else [{}])
        self.calls = []

    def RmStartSession(self, handle, flags, key):
        self.calls.append(("start", flags, len(key)))
        handle._obj.value = 77
        return self.start

    def RmRegisterResources(self, session, count, files, app_count, apps, service_count, services):
        self.calls.append(("register", session.value, count, files[0], app_count, apps, service_count, services))
        if isinstance(self.register, Exception):
            raise self.register
        return self.register

    def RmGetList(self, session, needed, count, processes, reasons):
        self.calls.append(("get", count._obj.value))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        identifiers = reply.get("process_ids", ())
        needed._obj.value = reply.get("needed", len(identifiers))
        count._obj.value = reply.get("count", len(identifiers))
        reasons._obj.value = reply.get("reasons", 0)
        if processes is not None:
            for index, identifier in enumerate(identifiers):
                processes[index].process.process_id = identifier
        return reply.get("code", 0)

    def RmEndSession(self, session):
        self.calls.append(("end", session.value))
        if isinstance(self.end, Exception):
            raise self.end
        return self.end


class FileUsageFailureTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mdrl-file-use-test-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "動画.mkv"
        self.path.write_bytes(b"isolated video fixture")

    def query(self, api):
        before = self.path.read_bytes()
        with patch.object(usage, "_load_restart_manager", return_value=api):
            result = usage._query_windows(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        return result

    def test_closed_file_registers_exactly_one_resource_and_ends_session(self):
        api = FakeRestartManager()
        result = self.query(api)
        self.assertEqual((result.in_use, result.known, result.process_ids), (False, True, ()))
        self.assertEqual(api.calls, [
            ("start", 0, 33), ("register", 77, 1, str(self.path), 0, None, 0, None),
            ("get", 0), ("end", 77),
        ])

    def test_more_data_retries_growth_and_preserves_own_and_external_pid(self):
        api = FakeRestartManager(replies=[
            {"code": 234, "needed": 1}, {"code": 234, "needed": 3},
            {"process_ids": (90001, os.getpid(), 90001), "reasons": 0x10},
        ])
        result = self.query(api)
        self.assertTrue(result.in_use)
        self.assertTrue(result.known)
        self.assertEqual(result.process_ids, tuple(sorted({90001, os.getpid()})))
        self.assertEqual([call for call in api.calls if call[0] == "get"], [("get", 0), ("get", 1), ("get", 3)])
        self.assertEqual(api.calls[-1], ("end", 77))

    def test_start_failure_does_not_end_a_session_that_was_not_started(self):
        api = FakeRestartManager(start=353)
        result = self.query(api)
        self.assertFalse(result.known)
        self.assertIn("353", result.message)
        self.assertEqual(api.calls, [("start", 0, 33)])

    def test_register_get_and_end_failures_all_fail_closed_and_end_session(self):
        for api in (
            FakeRestartManager(register=5), FakeRestartManager(register=OSError("register")),
            FakeRestartManager(replies=[{"code": 121}]), FakeRestartManager(replies=[OSError("get")]),
            FakeRestartManager(end=6), FakeRestartManager(end=OSError("end")),
        ):
            with self.subTest(api=api):
                result = self.query(api)
                self.assertFalse(result.known)
                self.assertEqual(api.calls[-1], ("end", 77))
                self.assertEqual(sum(call[0] == "end" for call in api.calls), 1)

    def test_end_failure_retains_observed_users_but_is_unknown(self):
        api = FakeRestartManager(end=121, replies=[{"code": 234, "needed": 1}, {"process_ids": (12,)}])
        result = self.query(api)
        self.assertEqual((result.in_use, result.known, result.process_ids), (True, False, (12,)))

    def test_changing_list_has_bounded_attempts_and_ends_session(self):
        api = FakeRestartManager(replies=[{"code": 234, "needed": index + 1} for index in range(usage._MAX_LIST_CALLS)])
        self.assertFalse(self.query(api).known)
        self.assertEqual(sum(call[0] == "get" for call in api.calls), usage._MAX_LIST_CALLS)
        self.assertEqual(api.calls[-1], ("end", 77))

    def test_invalid_oversized_or_incomplete_list_is_unknown(self):
        for replies in (
            [{"code": 234, "needed": 0}], [{"code": 234, "needed": usage._MAX_PROCESSES + 1}],
            [{"needed": 1, "count": 1}], [{"code": 234, "needed": 2}, {"needed": 2, "count": 1}],
            [{"code": 234, "needed": 1}, {"process_ids": (0,)}],
            [{"code": 234, "needed": 1}, {"process_ids": (0xFFFFFFFF,)}], [{"reasons": 1}],
        ):
            with self.subTest(replies=replies):
                api = FakeRestartManager(replies=replies)
                self.assertFalse(self.query(api).known)
                self.assertEqual(api.calls[-1], ("end", 77))

    def test_public_api_handles_loader_and_missing_file_failure(self):
        with patch.object(usage.sys, "platform", "win32"), patch.object(usage, "_load_restart_manager", side_effect=OSError("DLL")):
            self.assertFalse(usage.query_file_use(self.path).known)
        with patch.object(usage.sys, "platform", "win32"), patch.object(usage, "_load_restart_manager") as load:
            self.assertFalse(usage.query_file_use(self.path.parent / "missing.mkv").known)
            self.assertFalse(usage.query_file_use(self.path.parent).known)
            load.assert_not_called()

    def test_file_changed_during_observation_is_unknown(self):
        original_stat = self.path.stat()
        changed_stat = unittest.mock.Mock(st_dev=original_stat.st_dev, st_ino=original_stat.st_ino,
                                         st_size=original_stat.st_size + 1, st_mtime_ns=original_stat.st_mtime_ns)
        observed = usage.FileUseStatus(False, True, (), "no readers")
        with patch.object(usage.sys, "platform", "win32"), patch.object(usage.Path, "resolve", return_value=self.path), \
                patch.object(usage.Path, "stat", side_effect=[original_stat, changed_stat]), \
                patch.object(usage, "_query_windows", return_value=observed):
            self.assertFalse(usage.query_file_use(self.path).known)
        self.assertEqual(self.path.read_bytes(), b"isolated video fixture")

    def test_non_windows_keeps_existing_lease_and_os_failure_strategy(self):
        with patch.object(usage.sys, "platform", "linux"), patch.object(usage, "_query_windows") as native:
            result = usage.query_file_use(self.path)
        self.assertEqual((result.in_use, result.known, result.process_ids), (False, True, ()))
        native.assert_not_called()


_CHILD_READER = """
import ctypes
import os
import sys
if sys.argv[2] == 'python':
    with open(sys.argv[1], 'rb'):
        print(os.getpid(), flush=True)
        sys.stdin.readline()
else:
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.CreateFileW(sys.argv[1], 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        print(os.getpid(), flush=True)
        sys.stdin.readline()
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
"""


@unittest.skipUnless(sys.platform == "win32", "Windows Restart Manager integration")
class WindowsFileUsageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mdrl-file-use-native-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "動画.mkv"
        self.path.write_bytes(b"isolated native fixture")
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def assert_source_preserved(self):
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.digest)

    @contextmanager
    def child_reader(self, kind):
        # 自分で作成した隔離fixtureの子だけを使い、通常はstdinでハンドルを解放します。
        with subprocess.Popen([sys.executable, "-u", "-c", _CHILD_READER, str(self.path), kind],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW) as child:
            ready = queue.Queue()
            reader = threading.Thread(target=lambda: ready.put(child.stdout.readline().strip()), daemon=True)
            reader.start()
            try:
                reader_pid = int(ready.get(timeout=5))
                self.assertGreater(reader_pid, 0)
                yield reader_pid
            finally:
                try:
                    child.stdin.write("release\n")
                    child.stdin.flush()
                except BrokenPipeError:
                    pass
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # 異常試験時も所有するtest子を残さず、実際のプレイヤーには一切触れません。
                    child.kill()
                    child.wait(timeout=5)
                reader.join(timeout=5)
                self.assertFalse(reader.is_alive())
                errors = child.stderr.read()
                self.assertEqual(child.returncode, 0, errors)

    def test_closed_read_hash_handles_and_self_reader_are_correctly_observed(self):
        self.assertEqual(ctypes.sizeof(usage._ProcessInfo), 668)
        closed = usage.query_file_use(self.path)
        self.assertEqual((closed.in_use, closed.known, closed.process_ids), (False, True, ()))
        with self.path.open("rb"):
            opened = usage.query_file_use(self.path)
            self.assertTrue(opened.in_use)
            self.assertTrue(opened.known)
            self.assertIn(os.getpid(), opened.process_ids)
        self.assert_source_preserved()
        self.assertFalse(usage.query_file_use(self.path).in_use)

    def test_external_python_reader_and_shared_delete_handle_are_observed(self):
        for kind in ("python", "share_delete"):
            with self.subTest(kind=kind), self.child_reader(kind) as reader_pid:
                observed = usage.query_file_use(self.path)
                self.assertTrue(observed.known, observed.message)
                self.assertTrue(observed.in_use, observed.message)
                self.assertIn(reader_pid, observed.process_ids)
                self.assert_source_preserved()
            released = usage.query_file_use(self.path)
            self.assertTrue(released.known, released.message)
            self.assertFalse(released.in_use, released.message)

    def test_delete_guard_blocks_existing_and_new_readers_and_allows_file_removal(self):
        with self.path.open("rb"):
            with self.assertRaises(OSError):
                with usage.exclusive_delete_guard(self.path):
                    self.fail("既存の利用がある動画を取得してはいけません")
        staged = self.path.with_suffix(".pending")
        with usage.exclusive_delete_guard(self.path):
            with self.assertRaises(OSError):
                with self.path.open("rb"):
                    self.fail("削除区間中に読込を開始してはいけません")
            self.path.rename(staged)
            staged.unlink()
        self.assertFalse(self.path.exists())
        self.assertFalse(staged.exists())
