import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from master_duel_recorder_lite.recording_browsing import (
    RecordingBrowseError,
    RecordingBrowseFailure,
    RecordingBrowser,
)


class FakeRepository:
    def __init__(self, entry: object | None) -> None:
        self.entry = entry

    def get(self, recording_id: str) -> object | None:
        return self.entry if recording_id == "recording" else None


def entry(path: str, *, size: int | None = None) -> object:
    return SimpleNamespace(output_path=Path(path), size_bytes=size)


class RecordingBrowserTest(unittest.TestCase):
    def test_resolve_accepts_nonempty_recording_and_reports_size_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            video = root / "video.mkv"
            video.write_bytes(b"video")
            browser = RecordingBrowser(
                repository=FakeRepository(entry("video.mkv", size=99)),  # type: ignore[arg-type]
                recordings_root=root,
                system_name="Windows",
            )

            reference = browser.resolve("recording")

        self.assertEqual(reference.path, video.resolve())
        self.assertEqual(len(reference.warnings), 1)

    def test_missing_history_is_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            browser = RecordingBrowser(
                repository=FakeRepository(None),  # type: ignore[arg-type]
                recordings_root=Path(tmp_dir),
            )

            with self.assertRaises(RecordingBrowseError) as raised:
                browser.resolve("missing")

        self.assertIs(raised.exception.kind, RecordingBrowseFailure.NOT_FOUND)

    def test_reference_outside_recordings_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "recordings"
            root.mkdir()
            outside = root.parent / "outside.mkv"
            outside.write_bytes(b"video")
            browser = RecordingBrowser(
                repository=FakeRepository(entry("../outside.mkv")),  # type: ignore[arg-type]
                recordings_root=root,
            )

            with self.assertRaises(RecordingBrowseError) as raised:
                browser.resolve("recording")

        self.assertIs(raised.exception.kind, RecordingBrowseFailure.INVALID_REFERENCE)

    def test_missing_empty_and_unsupported_files_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            empty = root / "empty.mkv"
            empty.write_bytes(b"")
            text = root / "video.txt"
            text.write_bytes(b"video")
            cases = (
                ("missing.mkv", RecordingBrowseFailure.MISSING),
                ("empty.mkv", RecordingBrowseFailure.EMPTY),
                ("video.txt", RecordingBrowseFailure.UNSUPPORTED),
            )
            for relative, expected in cases:
                with self.subTest(relative=relative):
                    browser = RecordingBrowser(
                        repository=FakeRepository(entry(relative)),  # type: ignore[arg-type]
                        recordings_root=root,
                    )
                    with self.assertRaises(RecordingBrowseError) as raised:
                        browser.resolve("recording")
                    self.assertIs(raised.exception.kind, expected)

    def test_play_uses_windows_file_association(self) -> None:
        launched: list[str] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            video = root / "video.mp4"
            video.write_bytes(b"video")
            browser = RecordingBrowser(
                repository=FakeRepository(entry("video.mp4", size=5)),  # type: ignore[arg-type]
                recordings_root=root,
                system_name="Windows",
                start_file=launched.append,
            )

            browser.play("recording")

        self.assertEqual(launched, [str(video.resolve())])

    def test_reveal_uses_explorer_argument_list(self) -> None:
        commands: list[tuple[str, ...]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            video = root / "video.mkv"
            video.write_bytes(b"video")
            browser = RecordingBrowser(
                repository=FakeRepository(entry("video.mkv", size=5)),  # type: ignore[arg-type]
                recordings_root=root,
                system_name="Windows",
                process_launcher=lambda arguments: commands.append(tuple(arguments)),
            )

            browser.reveal("recording")

        self.assertEqual(commands[0][0], "explorer.exe")
        self.assertEqual(commands[0][1], f"/select,{video.resolve()}")

    def test_launch_failure_is_distinct(self) -> None:
        def fail(_path: str) -> None:
            raise OSError("association missing")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "video.mkv").write_bytes(b"video")
            browser = RecordingBrowser(
                repository=FakeRepository(entry("video.mkv")),  # type: ignore[arg-type]
                recordings_root=root,
                system_name="Windows",
                start_file=fail,
            )

            with self.assertRaises(RecordingBrowseError) as raised:
                browser.play("recording")

        self.assertIs(raised.exception.kind, RecordingBrowseFailure.LAUNCH_FAILED)


if __name__ == "__main__":
    unittest.main()
