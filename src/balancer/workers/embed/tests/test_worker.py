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
from pydantic import TypeAdapter, ValidationError

from batch import Batch
from embeddings import Embedding
from config import Config, DIMENSIONS
from image import EncodedImage
from task import ImageTask, Task, TextTask
from validation import BatchError, ConfigError, TaskError

ROOT = Path(__file__).resolve().parents[1]


def config() -> Config:
    """Загружает пример конфигурации воркера."""
    return Config.from_yaml(ROOT / "config.example.yaml")


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
        self.calls: list[TextTask | ImageTask] = []

    def embed(self, task: TextTask | ImageTask) -> list[float]:
        """Запоминает переданные задачи и возвращает тестовые векторы."""
        self.calls.append(task)
        return vector()


class FailingEmbedder(RecordingEmbedder):
    def embed(self, task: TextTask | ImageTask) -> list[float]:
        """Возвращает тестовые векторы при первом вызове, затем имитирует сбой GPU."""
        if self.calls:
            raise RuntimeError("GPU OOM")
        return super().embed(task)


class ContractTests(unittest.TestCase):
    def test_mixed_input_processed_individually_and_id_mapping(self) -> None:
        """Проверяет обработку задач по одной и привязку векторов к идентификаторам."""
        batch = Batch.from_json(StringIO(json.dumps([
            {"task_id": "image", "image": image_base64()},
            *[{"task_id": str(index), "text": "я" * 2000} for index in range(9)],
        ])))
        model = RecordingEmbedder()
        batch.embed(model)
        self.assertEqual(len(model.calls), 10)
        self.assertIsInstance(model.calls[0], ImageTask)
        self.assertTrue(all(isinstance(task, TextTask) for task in model.calls[1:]))
        self.assertEqual([task.task_id for task in model.calls], ["image", *map(str, range(9))])
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

    def test_failed_later_task_cannot_publish_partial_results(self) -> None:
        """Проверяет запрет частичного результата при сбое последующей задачи."""
        batch = Batch.from_json(StringIO(json.dumps([
            {"task_id": str(index), "text": "Текст"} for index in range(9)
        ])))
        with self.assertRaises(RuntimeError):
            batch.embed(FailingEmbedder())
        self.assertIsNone(batch.results)
        output = StringIO()
        with self.assertRaises(BatchError):
            batch.write(output)
        self.assertEqual(output.getvalue(), "")

    def test_invalid_model_outputs_rejected(self) -> None:
        """Проверяет отклонение некорректных векторов модели."""
        for value in ([], [0.0] * DIMENSIONS, [1.0],
                      [True] + [0.0] * (DIMENSIONS - 1),
                      [float("nan")] + [0.0] * (DIMENSIONS - 1),
                      [float("inf")] + [0.0] * (DIMENSIONS - 1)):
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValidationError):
                TypeAdapter(Embedding).validate_python({"vector": value})

    def test_config_rejects_missing_unknown_and_invalid_fields(self) -> None:
        """Проверяет отклонение неполной конфигурации, лишних полей и неверных значений."""
        valid = dict(vars(config()))
        invalid: list[dict[str, object]] = [
            {}, {**valid, "unknown": 1}, {**valid, "max_tokens": True},
            {**valid, "max_tokens": 0}, {**valid, "max_tokens": 32769},
            {**valid, "max_image_pixels": 4097}, {**valid, "model": "another/model"},
            {**valid, "device": "cpu", "compute_type": "bfloat16"},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ConfigError):
                Config.from_dict(value)

    def test_image_is_decoded_in_memory_as_rgb(self) -> None:
        """Проверяет декодирование изображения в памяти в формат RGB."""
        task = Task.from_dict({"task_id": "1", "image": image_base64()})
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
        task = Task.from_dict({
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
            Task.from_dict({
                "task_id": "1", "image": base64.b64encode(buffer.getvalue()).decode("ascii"),
            })

    def test_text_task_preserves_input(self) -> None:
        """Проверяет создание текстовой задачи с сохранением исходных пробелов."""
        task = Task.from_dict({"task_id": "text", "text": "  Привет\n"})
        self.assertEqual(task, TextTask("text", "  Привет\n"))

    def test_direct_construction_validates_data(self) -> None:
        """Проверяет ограничения при прямом создании объектов без методов разбора."""
        for task_id, text in (("", "Текст"), ("1", "  "), ("1", "я" * 2001), ("1", "\ud800")):
            with self.subTest(task_id=task_id, text_length=len(text)), self.assertRaises(ValidationError):
                TextTask(task_id, text)
        with self.assertRaises(ValidationError):
            EncodedImage(b"not an image")
        with self.assertRaises(ValidationError):
            Batch((TextTask("same", "Первый"), TextTask("same", "Второй")))
        with self.assertRaises(ValidationError):
            Batch(())

    def test_legacy_inference_settings_rejected(self) -> None:
        """Проверяет удаление настроек внутренних инференс-батчей из конфигурации."""
        for name in ("text_batch_size", "image_batch_size"):
            with self.subTest(name=name), self.assertRaises(ConfigError):
                Config.from_dict({**vars(config()), name: 8})

    def test_failed_image_cannot_publish_text_results(self) -> None:
        """Проверяет атомарность результата при сбое изображения после обработки текста."""
        batch = Batch.from_json(StringIO(json.dumps([
            {"task_id": "text", "text": "Текст"},
            {"task_id": "image", "image": image_base64()},
        ])))
        with self.assertRaises(RuntimeError):
            batch.embed(FailingEmbedder())
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
