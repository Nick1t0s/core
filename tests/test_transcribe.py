import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.balancer.workers.transcribe import config

Config = config.Config
with patch.dict("sys.modules", {"config": config}):
    from src.balancer.workers.transcribe.main import main


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "audio.wav"
        self.path.touch()
        self.tasks = [
            {"task_id": name, "input": {"path": str(self.path), "mime": "audio/wav"}}
            for name in ("first", "second")
        ]
        self.model = Mock()
        self.factory = Mock(return_value=self.model)
        self.addCleanup(patch.stopall)
        patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=self.factory)}).start()

    def run_worker(self, data, args=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        stdin = io.StringIO(json.dumps(data))
        with patch("sys.stdin", stdin), patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = main(args or [])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_batch_consumes_lazy_segments_and_loads_model_once(self):
        def segments():
            print("library diagnostic")
            yield SimpleNamespace(text=" Привет")
            yield SimpleNamespace(text=", мир!")

        self.model.transcribe.side_effect = [(segments(), None), (iter(()), None)]
        code, stdout, stderr = self.run_worker({"type": "transcribe", "tasks": self.tasks})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {"results": [
            {"task_id": "first", "payload": {"text": "Привет, мир!"}},
            {"task_id": "second", "payload": {"text": ""}},
        ]})
        self.assertIn("library diagnostic", stderr)
        self.factory.assert_called_once_with(
            "large-v3-turbo", device="cuda", compute_type="float16", local_files_only=False,
        )

    def test_lazy_failure_discards_whole_batch(self):
        def broken_segments():
            yield SimpleNamespace(text="partial")
            raise RuntimeError("decoder failed")

        self.model.transcribe.side_effect = [
            (iter([SimpleNamespace(text="completed")]), None), (broken_segments(), None),
        ]
        code, stdout, stderr = self.run_worker({"type": "transcribe", "tasks": self.tasks})
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("decoder failed", stderr)

    def test_invalid_batch_is_rejected_before_model_load(self):
        invalid = [None, {"type": "ocr", "tasks": self.tasks},
                   {"type": "transcribe", "tasks": []},
                   {"type": "transcribe", "tasks": [self.tasks[0], self.tasks[0]]}]
        for path in ("relative.wav", str(self.path.parent / "missing.wav")):
            invalid.append({"type": "transcribe", "tasks": [
                {"task_id": "bad", "input": {"path": path, "mime": "audio/wav"}},
            ]})
        for data in invalid:
            with self.subTest(data=data):
                code, stdout, stderr = self.run_worker(data)
                self.assertEqual(code, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr)
        self.factory.assert_not_called()

    def test_file_and_yaml_config(self):
        config_path = self.path.parent / "config.yaml"
        config_path.write_text("device: cpu\ncompute_type: int8\nlanguage: ru\n", encoding="utf-8")
        self.model.transcribe.return_value = (iter([SimpleNamespace(text=" Текст ")]), None)
        code, stdout, stderr = self.run_worker(None, [
            "--file", str(self.path), "--config", str(config_path),
        ])
        self.assertEqual((code, stdout, stderr), (0, "Текст\n", ""))
        self.factory.assert_called_once_with(
            "large-v3-turbo", device="cpu", compute_type="int8", local_files_only=False,
        )
        self.model.transcribe.assert_called_once_with(str(self.path), language="ru")

    def test_invalid_config(self):
        for data in (None, [], {"workers": {}}, {"model": ""}, {"device": "invalid"},
                     {"compute_type": False}, {"language": 12}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Config.from_dict(data)


if __name__ == "__main__":
    unittest.main()
