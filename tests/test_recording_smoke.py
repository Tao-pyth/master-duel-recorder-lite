import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from master_duel_recorder_lite.ffmpeg import discover_ffmpeg
from master_duel_recorder_lite.recording_session import RecordingSession, RecordingState


@unittest.skipUnless(
    os.getenv("MDRL_RUN_FFMPEG_SMOKE") == "1",
    "MDRL_RUN_FFMPEG_SMOKE=1 のときだけ実FFmpegを使用します",
)
class RealFfmpegSmokeTest(unittest.TestCase):
    def test_synthetic_video_and_audio_can_be_stopped_and_decoded(self) -> None:
        discovery = discover_ffmpeg("ffmpeg")
        if not discovery.found or discovery.executable is None:
            self.skipTest("FFmpegが見つかりません")

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "synthetic.mkv"
            command = (
                str(discovery.executable),
                "-hide_banner",
                "-loglevel",
                "error",
                "-n",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x240:rate=10",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:sample_rate=48000",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(output),
            )
            session = RecordingSession(command=command, output_path=output, startup_grace_seconds=0.2)
            self.assertIs(session.start(), RecordingState.RECORDING)
            time.sleep(0.5)
            result = session.stop(timeout_seconds=5.0)

            self.assertTrue(result.succeeded, result.error)
            self.assertGreater(result.size_bytes, 0)
            decode = subprocess.run(
                [
                    str(discovery.executable),
                    "-v",
                    "error",
                    "-i",
                    str(output),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-f",
                    "null",
                    "-",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            self.assertEqual(decode.returncode, 0, decode.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
