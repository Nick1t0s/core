"""Контракт процесса и атомарность обработки без загрузки весов."""

import base64
from dataclasses import replace
from io import BytesIO, StringIO
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from PIL import Image

from batch import Batch
from config import Config, DIMENSIONS
from image import EncodedImage
from inference import InferenceBatch, InferenceImageBatch, InferenceTextBatch, validate_vectors
from task import ImageTask, Task, TextTask, task_from_dict
from validation import BatchError, ConfigError, TaskError

ROOT = Path(__file__).resolve().parents[1]


def config() -> Config:
    """Загружает пример конфигурации с фиксированными размерами тестовых батчей."""
    return replace(Config.from_yaml(ROOT / "config.example.yaml"), text_batch_size=8, image_batch_size=1)


def image_base64() -> str:
    """Создаёт тестовое красное PNG-изображение и кодирует его в base64."""
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "red").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def vector() -> list[float]:
    """Создаёт нормализованный тестовый вектор размерности модели."""
    return [1.0] + [0.0] * (DIMENSIONS - 1)


class RecordingEmbedder:
    def __init__(self) -> None:
        """Создаёт пустой список вызовов тестового эмбеддера."""
        self.calls: list[InferenceBatch[Task]] = []

    def embed(self, batch: InferenceBatch[Task]) -> list[list[float]]:
        """Запоминает переданные задачи и возвращает тестовые векторы."""
        self.calls.append(batch)
        return [vector() for _ in batch.tasks]


class FailingEmbedder(RecordingEmbedder):
    def embed(self, batch: InferenceBatch[Task]) -> list[list[float]]:
        """Возвращает тестовые векторы при первом вызове, затем имитирует сбой GPU."""
        if self.calls:
            raise RuntimeError("GPU OOM")
        return super().embed(batch)


class ContractTests(unittest.TestCase):
    def test_mixed_input_separate_batches_and_id_mapping(self) -> None:
        """Проверяет разделение входов на батчи и привязку векторов к задачам."""
        batch = Batch.from_json(StringIO(json.dumps([
            {"task_id": "image", "image": image_base64()},
            *[{"task_id": str(index), "text": "я" * 2000} for index in range(9)],
        ])))
        model = RecordingEmbedder()
        batch.embed(config(), model)
        self.assertEqual([len(group.tasks) for group in model.calls], [8, 1, 1])
        self.assertTrue(all(isinstance(group, InferenceTextBatch) for group in model.calls[:2]))
        self.assertIsInstance(model.calls[-1], InferenceImageBatch)
        self.assertEqual([task.task_id for group in model.calls[:2] for task in group.tasks], list(map(str, range(9))))
        self.assertEqual(model.calls[-1].tasks[0].task_id, "image")
        output = StringIO()
        batch.write(output)
        data: object = json.loads(output.getvalue())
        self.assertIsInstance(data, dict)
        self.assertEqual(batch.results and set(batch.results), {"image", *map(str, range(9))})
        self.assertIn('"embed_model": "Qwen3-VL-Embedding-2B"', output.getvalue())
        self.assertIn('"embeddings":', output.getvalue())

    def test_bad_inputs_rejected(self) -> None:
        """Проверяет отклонение некорректных пачек и входных данных задач."""
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
        """Проверяет запрет частичного результата при сбое последующего батча."""
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
        """Проверяет отклонение некорректных векторов модели."""
        for value in ([], [[0.0] * DIMENSIONS], [[1.0]],
                      [[True] + [0.0] * (DIMENSIONS - 1)],
                      [[float("nan")] + [0.0] * (DIMENSIONS - 1)],
                      [[float("inf")] + [0.0] * (DIMENSIONS - 1)]):
            with self.subTest(value=str(value)[:30]), self.assertRaises(BatchError):
                validate_vectors(value, 1)

    def test_config_rejects_missing_unknown_and_invalid_fields(self) -> None:
        """Проверяет отклонение неполной конфигурации, лишних полей и неверных значений."""
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
        """Проверяет декодирование изображения в памяти в формат RGB."""
        task = task_from_dict({"task_id": "1", "image": image_base64()})
        self.assertIsInstance(task, ImageTask)
        assert isinstance(task, ImageTask)
        self.assertIsInstance(task.image, EncodedImage)
        with task.image.open() as image:
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.size, (64, 64))

    def test_exif_orientation_is_applied(self) -> None:
        """Проверяет поворот изображения по EXIF-ориентации."""
        buffer = BytesIO()
        image = Image.new("RGB", (32, 64), "red")
        exif = Image.Exif()
        exif[274] = 6
        image.save(buffer, format="JPEG", exif=exif)
        task = task_from_dict({
            "task_id": "1", "image": base64.b64encode(buffer.getvalue()).decode("ascii"),
        })
        assert isinstance(task, ImageTask)
        with task.image.open() as decoded:
            self.assertEqual(decoded.size, (64, 32))

    def test_animated_image_is_rejected(self) -> None:
        """Проверяет отклонение многокадрового изображения."""
        buffer = BytesIO()
        Image.new("RGB", (32, 32), "red").save(
            buffer, format="GIF", save_all=True,
            append_images=[Image.new("RGB", (32, 32), "blue")],
        )
        with self.assertRaises(TaskError):
            task_from_dict({
                "task_id": "1", "image": base64.b64encode(buffer.getvalue()).decode("ascii"),
            })

    def test_text_task_preserves_input(self) -> None:
        """Проверяет создание текстовой задачи с сохранением исходных пробелов."""
        task = task_from_dict({"task_id": "text", "text": "  Привет\n"})
        self.assertEqual(task, TextTask("text", "  Привет\n"))

    def test_single_modality_uses_only_its_batch_type(self) -> None:
        """Проверяет отсутствие пустых батчей и соблюдение обоих лимитов размера."""
        settings = replace(config(), text_batch_size=2, image_batch_size=2)
        for content, source, batch_type in (
            ("text", "Текст", InferenceTextBatch),
            ("image", image_base64(), InferenceImageBatch),
        ):
            with self.subTest(content=content):
                batch = Batch.from_json(StringIO(json.dumps([
                    {"task_id": str(index), content: source} for index in range(5)
                ])))
                model = RecordingEmbedder()
                batch.embed(settings, model)
                self.assertEqual([len(group.tasks) for group in model.calls], [2, 2, 1])
                self.assertTrue(all(isinstance(group, batch_type) for group in model.calls))
                self.assertEqual(
                    [task.task_id for group in model.calls for task in group.tasks],
                    list(map(str, range(5))),
                )

    def test_failed_image_batch_cannot_publish_text_results(self) -> None:
        """Проверяет атомарность результата при сбое изображения после обработки текста."""
        batch = Batch.from_json(StringIO(json.dumps([
            {"task_id": "text", "text": "Текст"},
            {"task_id": "image", "image": image_base64()},
        ])))
        with self.assertRaises(RuntimeError):
            batch.embed(config(), FailingEmbedder())
        self.assertIsNone(batch.results)
        with self.assertRaises(BatchError):
            batch.write(StringIO())

    def test_cli_invalid_input_has_empty_stdout(self) -> None:
        """Проверяет код ошибки и пустой stdout при некорректном входе CLI."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "src/main.py"), "--config", str(ROOT / "config.example.yaml")],
            input="[]", text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("embed:", result.stderr)

    def test_cli_requires_config(self) -> None:
        """Проверяет обязательность аргумента конфигурации в CLI."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "src/main.py")],
            input="[]", text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_entry_point_outputs_result_only(self) -> None:
        """Проверяет, что stdout содержит результат, а диагностика попадает в stderr."""
        import main

        class NoisyEmbedder(RecordingEmbedder):
            def __init__(self, settings: Config) -> None:
                """Создаёт тестовый эмбеддер и печатает диагностику загрузки."""
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
