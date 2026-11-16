"""Пачка текстов и изображений в памяти, без входных файлов."""

import base64
import binascii
from dataclasses import dataclass, field
from io import BytesIO
import json
import math
from typing import Protocol, TextIO

from PIL import Image, ImageOps

from config import Config, DIMENSIONS, MAX_TEXT_CHARACTERS, MODEL_NAME
from validation import BatchError, TaskError, fields, invalid_constant, nonempty_string, unique_object


@dataclass(frozen=True)
class Task:
    task_id: str
    text: str | None
    image_bytes: bytes | None

    @classmethod
    def from_dict(cls, value: object) -> "Task":
        if not isinstance(value, dict):
            raise TaskError("Задача должна быть JSON-объектом")
        content = "text" if "text" in value else "image"
        data = fields(value, {"task_id", content}, TaskError)
        task_id = nonempty_string(data["task_id"], "task_id", TaskError)
        source = nonempty_string(data[content], content, TaskError)
        if content == "text":
            if len(source) > MAX_TEXT_CHARACTERS:
                raise TaskError("text: максимум 2000 символов, включая пробелы")
            try:
                source.encode("utf-8")
            except UnicodeError as exc:
                raise TaskError("text: некорректный Unicode") from exc
            return cls(task_id, source, None)
        try:
            image_bytes = base64.b64decode(source, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise TaskError("image: ожидается стандартная base64 без data URI") from exc
        task = cls(task_id, None, image_bytes)
        # Проверить все изображения до загрузки модели, не сохраняя декодированные копии.
        with task.open_image():
            pass
        return task

    def open_image(self) -> Image.Image:
        if self.image_bytes is None:
            raise TaskError("У текстовой задачи нет изображения")
        try:
            with Image.open(BytesIO(self.image_bytes)) as source:
                if getattr(source, "n_frames", 1) != 1:
                    raise TaskError("Одна задача должна содержать одно неподвижное изображение")
                source.load()
                return ImageOps.exif_transpose(source).convert("RGB")
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise TaskError("image: невозможно декодировать изображение") from exc


class Embedder(Protocol):
    def embed(self, tasks: tuple[Task, ...]) -> list[list[float]]: ...


def validate_vectors(value: object, expected_count: int) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise BatchError("Число эмбеддингов не совпадает с числом задач")
    result: list[list[float]] = []
    for row in value:
        if not isinstance(row, list) or len(row) != DIMENSIONS:
            raise BatchError("Эмбеддинг должен содержать ровно 2048 чисел")
        vector: list[float] = []
        for number in row:
            if isinstance(number, bool) or not isinstance(number, (float, int)):
                raise BatchError("Эмбеддинг должен содержать только конечные числа")
            try:
                converted = float(number)
            except OverflowError as exc:
                raise BatchError("Число эмбеддинга выходит за допустимый диапазон") from exc
            if not math.isfinite(converted):
                raise BatchError("Эмбеддинг должен содержать только конечные числа")
            vector.append(converted)
        norm = math.hypot(*vector)
        if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise BatchError("Эмбеддинг должен быть нормализован")
        result.append(vector)
    return result


@dataclass
class Batch:
    tasks: tuple[Task, ...]
    results: dict[str, list[float]] | None = field(default=None, init=False)

    @classmethod
    def from_json(cls, stream: TextIO) -> "Batch":
        try:
            data: object = json.load(
                stream, object_pairs_hook=unique_object, parse_constant=invalid_constant,
            )
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise BatchError("Не удалось прочитать JSON-пачку") from exc
        if not isinstance(data, list) or not data:
            raise BatchError("Ожидается непустой массив задач")
        tasks = tuple(Task.from_dict(item) for item in data)
        if len({task.task_id for task in tasks}) != len(tasks):
            raise BatchError("task_id должен быть уникальным в пачке")
        return cls(tasks)

    def embed(self, config: Config, model: Embedder) -> None:
        self.results = None
        results: dict[str, list[float]] = {}
        for is_text, size in ((True, config.text_batch_size), (False, config.image_batch_size)):
            tasks = tuple(task for task in self.tasks if (task.text is not None) == is_text)
            for start in range(0, len(tasks), size):
                group = tasks[start:start + size]
                vectors = validate_vectors(model.embed(group), len(group))
                results.update((task.task_id, vector) for task, vector in zip(group, vectors, strict=True))
        self.results = results

    def write(self, stream: TextIO) -> None:
        if self.results is None:
            raise BatchError("Пачка ещё не обработана")
        document = json.dumps(
            {"embed_model": MODEL_NAME, "embeddings": self.results},
            ensure_ascii=False, allow_nan=False,
        )
        stream.write(document + "\n")
