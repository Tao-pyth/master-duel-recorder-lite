import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from master_duel_recorder_lite.__main__ import main


class ConfigCliTest(unittest.TestCase):
    def test_init_set_get_show_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--user-data-dir", str(root), "config", "init"]), 0)
                self.assertEqual(
                    main(
                        [
                            "--user-data-dir",
                            str(root),
                            "config",
                            "set",
                            "recorder.frame_rate",
                            "60",
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            "--user-data-dir",
                            str(root),
                            "config",
                            "get",
                            "recorder.frame_rate",
                            "--json",
                        ]
                    ),
                    0,
                )
            get_document = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(get_document, {"key": "recorder.frame_rate", "value": 60})

            show_output = io.StringIO()
            with redirect_stdout(show_output):
                self.assertEqual(
                    main(["--user-data-dir", str(root), "config", "show", "--json"]),
                    0,
                )
            show_document = json.loads(show_output.getvalue())
            self.assertEqual(show_document["values"]["recorder.frame_rate"], 60)
            self.assertNotIn("path", show_document)

            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["--user-data-dir", str(root), "config", "reset", "--yes"]),
                    0,
                )
            self.assertTrue((root / "config" / "app.toml.previous").is_file())

    def test_invalid_set_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--user-data-dir", str(root), "config", "init"]), 0)
            config_path = root / "config" / "app.toml"
            original = config_path.read_bytes()
            error = io.StringIO()

            with redirect_stderr(error):
                code = main(
                    [
                        "--user-data-dir",
                        str(root),
                        "config",
                        "set",
                        "recorder.frame_rate",
                        "0",
                    ]
                )

            self.assertEqual(code, 2)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertIn("E_CONFIG_VALUE", error.getvalue())
            self.assertIn("対処:", error.getvalue())

    def test_init_does_not_overwrite_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--user-data-dir", str(root), "config", "init"]), 0)
            original = (root / "config" / "app.toml").read_bytes()
            with redirect_stderr(io.StringIO()):
                code = main(["--user-data-dir", str(root), "config", "init"])

            self.assertEqual(code, 4)
            self.assertEqual((root / "config" / "app.toml").read_bytes(), original)

    def test_secret_like_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(
                    [
                        "--user-data-dir",
                        tmp_dir,
                        "config",
                        "set",
                        "upload.oauth_token",
                        "secret",
                    ]
                )

        self.assertEqual(code, 2)
        self.assertIn("E_CONFIG_VALUE", error.getvalue())

    def test_reset_recovers_invalid_config_and_preserves_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            config_path = root / "config" / "app.toml"
            config_path.parent.mkdir(parents=True)
            invalid = b"[recorder\ninvalid"
            config_path.write_bytes(invalid)

            with redirect_stdout(io.StringIO()):
                code = main(["--user-data-dir", str(root), "config", "reset", "--yes"])
            previous = config_path.with_name("app.toml.previous").read_bytes()
            show_output = io.StringIO()
            with redirect_stdout(show_output):
                show_code = main(
                    ["--user-data-dir", str(root), "config", "show", "--json"]
                )

        self.assertEqual((code, show_code), (0, 0))
        self.assertEqual(previous, invalid)
        self.assertEqual(json.loads(show_output.getvalue())["values"]["recorder.frame_rate"], 30)


if __name__ == "__main__":
    unittest.main()
