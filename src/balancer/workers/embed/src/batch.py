"""Пачка текстов и изображений в памяти, без входных файлов."""

from dataclasses import dataclass, field
from functools import partial
import json
from typing import Iterator, TextIO

from config import Config, MODEL_NAME
from inference import Embedder, InferenceBatch, InferenceImageBatch, InferenceTextBatch
from task import ImageTask, Task, TextTask, task_from_dict
from validation import BatchError, invalid_constant, unique_object


@dataclass
class Batch:
    tasks: tuple[TextTask | ImageTask, ...]
    results: dict[str, list[float]] | None = field(default=None, init=False)

    @classmethod
    def from_json(cls, stream: TextIO) -> "Batch":
        """Читает JSON-пачку и проверяет задачи и уникальность их идентификаторов."""
        try:
            data: object = json.load(
                stream,
                object_pairs_hook=partial(unique_object, error=BatchError),
                parse_constant=partial(invalid_constant, error=BatchError),
            )
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise BatchError("Не удалось прочитать JSON-пачку") from exc
        if not isinstance(data, list) or not data:
            raise BatchError("Ожидается непустой массив задач")
        tasks = tuple(task_from_dict(item) for item in data)
        if len({task.task_id for task in tasks}) != len(tasks):
            raise BatchError("task_id должен быть уникальным в пачке")
        return cls(tasks)

    def embed(self, config: Config, model: Embedder) -> None:
        """Обрабатывает текстовые и графические батчи и сохраняет полный результат."""
        self.results = None
        results: dict[str, list[float]] = {}
        for group in self.inference_batches(config):
            results.update(group.embed(model))
        self.results = results

    def inference_batches(self, config: Config) -> Iterator[InferenceBatch[Task]]:
        """Выдаёт однородные батчи заданного размера: сначала тексты, затем изображения."""
        texts = tuple(task for task in self.tasks if isinstance(task, TextTask))
        for start in range(0, len(texts), config.text_batch_size):
            yield InferenceTextBatch(texts[start:start + config.text_batch_size])
        images = tuple(task for task in self.tasks if isinstance(task, ImageTask))
        for start in range(0, len(images), config.image_batch_size):
            yield InferenceImageBatch(images[start:start + config.image_batch_size])

    def write(self, stream: TextIO) -> None:
        """Записывает модель и эмбеддинги обработанной пачки в поток как JSON."""
        if self.results is None:
            raise BatchError("Пачка ещё не обработана")
        document = json.dumps(
            {"embed_model": MODEL_NAME, "embeddings": self.results},
            ensure_ascii=False, allow_nan=False,
        )
        stream.write(document + "\n")
