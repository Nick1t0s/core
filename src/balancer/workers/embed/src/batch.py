"""Входная пачка и последовательная обработка задач."""

from dataclasses import field
from functools import partial
import json
from typing import Annotated, Protocol, TextIO

from pydantic import Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from config import MODEL_NAME
from embeddings import Embedding
from task import ImageTask, TextTask
from validation import BatchError, VALIDATION_CONFIG, invalid_constant, unique_object


class Embedder(Protocol):
    def embed(self, task: TextTask | ImageTask) -> list[float]:
        """Вычисляет вектор одной текстовой или графической задачи."""
        ...


@dataclass(config=VALIDATION_CONFIG)
class Batch:
    tasks: Annotated[tuple[TextTask | ImageTask, ...], Field(min_length=1)]
    results: dict[str, list[float]] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Проверяет уникальность идентификаторов после проверки всех задач."""
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise BatchError("task_id должен быть уникальным в пачке")

    @classmethod
    def from_json(cls, stream: TextIO) -> "Batch":
        """Читает JSON-массив и создаёт пачку с автоматической проверкой задач."""
        try:
            data: object = json.load(
                stream,
                object_pairs_hook=partial(unique_object, error=BatchError),
                parse_constant=partial(invalid_constant, error=BatchError),
            )
            return TypeAdapter(cls).validate_python({"tasks": data})
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise BatchError("Не удалось прочитать JSON-пачку") from exc

    def embed(self, model: Embedder) -> None:
        """Последовательно вычисляет векторы и сохраняет только полный результат."""
        self.results = None
        results: dict[str, list[float]] = {}
        for task in self.tasks:
            try:
                embedding = Embedding(model.embed(task))
            except ValidationError as exc:
                raise BatchError(str(exc)) from exc
            results[task.task_id] = embedding.vector
        self.results = results

    def write(self, stream: TextIO) -> None:
        """Записывает JSON только после успешной обработки всей пачки."""
        if self.results is None:
            raise BatchError("Пачка ещё не обработана")
        document = json.dumps(
            {"embed_model": MODEL_NAME, "embeddings": self.results},
            ensure_ascii=False, allow_nan=False,
        )
        stream.write(document + "\n")
