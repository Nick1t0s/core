"""Контракт base64-аудио и передача файлового объекта в faster-whisper."""

import base64
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import BytesIO, StringIO
import json
from pathlib import Path
import subprocess
import sys
from typing import ClassVar, Iterator
import unittest
from unittest.mock import patch

from batch import Batch
from config import Config
from validation import BatchError, TaskError

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Segment:
    start: float
    text: str


class FakeWhisper:
    instances: ClassVar[list["FakeWhisper"]] = []

    def __init__(self, model: str, device: str, compute_type: str, local_files_only: bool) -> None:
        self.calls: list[tuple[BytesIO, str | None]] = []
        self.local_files_only = local_files_only
        self.instances.append(self)

    def transcribe(self, audio: BytesIO, language: str | None) -> tuple[Iterator[Segment], None]:
        if not isinstance(audio, BytesIO):
            raise AssertionError("Ожидается BytesIO, а не путь к файлу")
        self.calls.append((audio, language))

        def segments() -> Iterator[Segment]:
            data = audio.read()
            if data == b"broken audio":
                raise RuntimeError("Не удалось декодировать аудио")
            if data != b"silence":
                yield Segment(70.9, "  Привет,\n  мир!  ")
                yield Segment(75.2, "   ")
                yield Segment(3601.7, "Следующее предложение.")

        return segments(), None


def task(task_id: str, audio: bytes) -> dict[str, str]:
    return {"task_id": task_id, "audio": base64.b64encode(audio).decode("ascii")}


def config() -> Config:
    return Config.from_yaml(ROOT / "config.example.yaml")


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeWhisper.instances.clear()

    def test_audio_bytes_round_trip(self) -> None:
        audio = bytes(range(256))
        batch = Batch.from_json(StringIO(json.dumps([task("1", audio)])))
        self.assertEqual(batch.tasks[0].audio, audio)

    def test_rejects_invalid_inputs_and_old_paths(self) -> None:
        invalid = [
            "[]", "{}", '[{"task_id":"1","path":"/tmp/audio.wav"}]',
            '[{"task_id":"1","audio":""}]',
            '[{"task_id":"1","audio":"  "}]',
            '[{"task_id":"1","audio":"data:audio/wav;base64,YQ=="}]',
            '[{"task_id":"1","audio":"Y Q=="}]',
            '[{"task_id":"1","audio":12}]',
            '[{"task_id":"1","audio":"YQ==","path":"/tmp/a"}]',
            '[{"task_id":"1","audio":"YQ==","audio":"Yg=="}]',
            '[{"task_id":"1","audio":NaN}]',
            json.dumps([task("1", b"a"), task("1", b"b")]),
        ]
        for source in invalid:
            with self.subTest(source=source), self.assertRaises((TaskError, BatchError)):
                Batch.from_json(StringIO(source))

    def test_model_loaded_once_and_streams_stay_open_for_lazy_segments(self) -> None:
        batch = Batch.from_list([task("speech", b"speech"), task("quiet", b"silence")])
        with patch("faster_whisper.WhisperModel", FakeWhisper):
            batch.transcribe(config())
        self.assertEqual(len(FakeWhisper.instances), 1)
        model = FakeWhisper.instances[0]
        self.assertTrue(model.local_files_only)
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(all(stream.closed for stream, _ in model.calls))
        self.assertEqual(batch.results, {
            "speech": "[00:01:10] Привет, мир!\n[01:00:01] Следующее предложение.",
            "quiet": "",
        })
        output = StringIO()
        with redirect_stdout(output):
            batch.write()
        self.assertEqual(json.loads(output.getvalue()), batch.results)

    def test_failed_second_audio_cannot_publish_partial_result(self) -> None:
        import main

        output = StringIO()
        errors = StringIO()
        source = json.dumps([task("1", b"speech"), task("2", b"broken audio")])
        with patch("faster_whisper.WhisperModel", FakeWhisper), \
             patch("sys.stdin", StringIO(source)), patch("sys.stdout", output), \
             patch("sys.stderr", errors):
            code = main.main(["--config", str(ROOT / "config.example.yaml")])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("transcribe:", errors.getvalue())
        self.assertTrue(all(stream.closed for stream, _ in FakeWhisper.instances[0].calls))

    def test_cli_rejects_invalid_base64_before_loading_model(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "src/main.py"), "--config", str(ROOT / "config.example.yaml")],
            input='[{"task_id":"1","audio":"not base64"}]',
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("base64", result.stderr)


if __name__ == "__main__":
    unittest.main()
