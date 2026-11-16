"""Контракт процесса и атомарность обработки без загрузки весов."""

import base64
from io import BytesIO, StringIO
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from PIL import Image

from batch import Batch, Task, validate_vectors
from config import Config, DIMENSIONS
from validation import BatchError, ConfigError, TaskError

ROOT = Path(__file__).resolve().parents[1]


def config() -> Config:
    return Config.from_yaml(ROOT / "config.example.yaml")


def image_base64() -> str:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "red").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def vector() -> list[float]:
    return [1.0] + [0.0] * (DIMENSIONS - 1)


class RecordingEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[Task, ...]] = []

    def embed(self, tasks: tuple[Task, ...]) -> list[list[float]]:
        self.calls.append(tasks)
        return [vector() for _ in tasks]


class FailingEmbedder(RecordingEmbedder):
    def embed(self, tasks: tuple[Task, ...]) -> list[list[float]]:
        if self.calls:
            raise RuntimeError("GPU OOM")
        return super().embed(tasks)


class ContractTests(unittest.TestCase):
    def test_mixed_input_separate_batches_and_id_mapping(self) -> None:
        batch = Batch.from_json(StringIO(json.dumps([
            {"task_id": "image", "image": image_base64()},
            *[{"task_id": str(index), "text": "я" * 2000} for index in range(9)],
        ])))
        model = RecordingEmbedder()
        batch.embed(config(), model)
        self.assertEqual([len(group) for group in model.calls], [8, 1, 1])
        self.assertTrue(all(task.text is not None for task in model.calls[0]))
        self.assertIsNone(model.calls[-1][0].text)
        output = StringIO()
        batch.write(output)
        data: object = json.loads(output.getvalue())
        self.assertIsInstance(data, dict)
        self.assertEqual(batch.results and set(batch.results), {"image", *map(str, range(9))})
        self.assertIn('"embed_model": "Qwen3-VL-Embedding-2B"', output.getvalue())
        self.assertIn('"embeddings":', output.getvalue())

    def test_bad_inputs_rejected(self) -> None:
        cases = [
            "[]", "{}", '{"type":"embed","tasks":[]}',
            '[{"task_id":"1","text":"a","image":"AA=="}]',
            '[{"task_id":"1","path":"/tmp/text"}]',
            '[{"task_id":"1","text":"a"},{"task_id":"1","text":"b"}]',
            '[{"task_id":"1","text":"a","text":"b"}]',
            '[{"task_id":"1","text":NaN}]',
            '[{"task_id":"1","text":"  "}]',
            '[{"task_id":"1","image":"data:image/png;base64,AA=="}]',
            '[{"task_id":"1","image":"aGVsbG8="}]',
            json.dumps([{"task_id": "1", "text": "я" * 2001}]),
        ]
        for source in cases:
            with self.subTest(source=source[:90]), self.assertRaises((TaskError, BatchError)):
                Batch.from_json(StringIO(source))

    def test_failed_later_group_cannot_publish_partial_results(self) -> None:
        batch = Batch.from_json(StringIO(json.dumps([
            {"task_id": str(index), "text": "Текст"} for index in range(9)
        ])))
        with self.assertRaises(RuntimeError):
            batch.embed(config(), FailingEmbedder())
        self.assertIsNone(batch.results)
        output = StringIO()
        with self.assertRaises(BatchError):
            batch.write(output)
        self.assertEqual(output.getvalue(), "")

    def test_invalid_model_outputs_rejected(self) -> None:
        for value in ([], [[0.0] * DIMENSIONS], [[1.0]],
                      [[True] + [0.0] * (DIMENSIONS - 1)],
                      [[float("nan")] + [0.0] * (DIMENSIONS - 1)],
                      [[float("inf")] + [0.0] * (DIMENSIONS - 1)]):
            with self.subTest(value=str(value)[:30]), self.assertRaises(BatchError):
                validate_vectors(value, 1)

    def test_config_rejects_missing_unknown_and_invalid_fields(self) -> None:
        valid = dict(vars(config()))
        invalid: list[dict[str, object]] = [
            {}, {**valid, "unknown": 1}, {**valid, "text_batch_size": True},
            {**valid, "image_batch_size": 0}, {**valid, "max_tokens": 32769},
            {**valid, "max_image_pixels": 4097}, {**valid, "model": "another/model"},
            {**valid, "device": "cpu", "compute_type": "bfloat16"},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ConfigError):
                Config.from_dict(value)

    def test_image_is_decoded_in_memory_as_rgb(self) -> None:
        task = Task.from_dict({"task_id": "1", "image": image_base64()})
        with task.open_image() as image:
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.size, (64, 64))

    def test_exif_orientation_is_applied(self) -> None:
        buffer = BytesIO()
        image = Image.new("RGB", (32, 64), "red")
        exif = Image.Exif()
        exif[274] = 6
        image.save(buffer, format="JPEG", exif=exif)
        task = Task.from_dict({
            "task_id": "1", "image": base64.b64encode(buffer.getvalue()).decode("ascii"),
        })
        with task.open_image() as decoded:
            self.assertEqual(decoded.size, (64, 32))

    def test_animated_image_is_rejected(self) -> None:
        buffer = BytesIO()
        Image.new("RGB", (32, 32), "red").save(
            buffer, format="GIF", save_all=True,
            append_images=[Image.new("RGB", (32, 32), "blue")],
        )
        with self.assertRaises(TaskError):
            Task.from_dict({
                "task_id": "1", "image": base64.b64encode(buffer.getvalue()).decode("ascii"),
            })

    def test_cli_invalid_input_has_empty_stdout(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "src/main.py"), "--config", str(ROOT / "config.example.yaml")],
            input="[]", text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("embed:", result.stderr)

    def test_cli_requires_config(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "src/main.py")],
            input="[]", text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_entry_point_outputs_result_only(self) -> None:
        import main

        class NoisyEmbedder(RecordingEmbedder):
            def __init__(self, settings: Config) -> None:
                super().__init__()
                print("model load diagnostic")

        output = StringIO()
        errors = StringIO()
        with patch("sys.stdin", StringIO('[{"task_id":"1","text":"Привет"}]')), \
             patch("sys.stdout", output), patch("sys.stderr", errors), \
             patch("model.QwenEmbedder", NoisyEmbedder):
            code = main.main(["--config", str(ROOT / "config.example.yaml")])
        self.assertEqual(code, 0)
        self.assertIn("model load diagnostic", errors.getvalue())
        self.assertNotIn("diagnostic", output.getvalue())
        self.assertIn('"embed_model":', output.getvalue())


if __name__ == "__main__":
    unittest.main()
