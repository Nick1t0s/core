"""Однородные батчи для одного вызова модели и проверка эмбеддингов."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import ExitStack
from dataclasses import dataclass
import math
from typing import Generic, Protocol, TYPE_CHECKING, TypeVar

from config import Config, DIMENSIONS
from task import ImageTask, Task, TextTask
from validation import BatchError

if TYPE_CHECKING:
    from transformers import BatchFeature, Qwen3VLProcessor


TaskT_co = TypeVar("TaskT_co", bound=Task, covariant=True)


class Embedder(Protocol):
    def embed(self, batch: InferenceBatch[Task]) -> list[list[float]]:
        """Возвращает векторы задач внутреннего батча в исходном порядке."""
        ...


@dataclass(frozen=True)
class InferenceBatch(ABC, Generic[TaskT_co]):
    tasks: tuple[TaskT_co, ...]

    def embed(self, model: Embedder) -> dict[str, list[float]]:
        """Вычисляет и проверяет векторы, сопоставляя их идентификаторам задач."""
        vectors = validate_vectors(model.embed(self), len(self.tasks))
        return {task.task_id: vector for task, vector in zip(self.tasks, vectors, strict=True)}

    @abstractmethod
    def prepare(self, processor: Qwen3VLProcessor, config: Config) -> BatchFeature:
        """Подготавливает тензоры однородного батча для модели."""
        ...

    def prompts(
        self,
        processor: Qwen3VLProcessor,
        instruction: str,
        contents: list[list[dict[str, str]]],
    ) -> list[str]:
        """Применяет шаблон диалога с общей инструкцией к содержимому задач."""
        conversations: list[list[dict[str, object]]] = [
            [
                {"role": "system", "content": [{"type": "text", "text": instruction}]},
                {"role": "user", "content": content},
            ]
            for content in contents
        ]
        prompts: object = processor.apply_chat_template(
            conversations, tokenize=False, add_generation_prompt=True,
        )
        if not isinstance(prompts, list) or not all(isinstance(item, str) for item in prompts):
            raise BatchError("Процессор вернул неверный формат промптов")
        return prompts


class InferenceTextBatch(InferenceBatch[TextTask]):
    def prepare(self, processor: Qwen3VLProcessor, config: Config) -> BatchFeature:
        """Токенизирует тексты с инструкцией и padding без обрезания."""
        contents = [[{"type": "text", "text": task.text}] for task in self.tasks]
        prompts = self.prompts(processor, config.instruction, contents)
        return processor(text=prompts, padding=True, truncation=False, return_tensors="pt")


class InferenceImageBatch(InferenceBatch[ImageTask]):
    def prepare(self, processor: Qwen3VLProcessor, config: Config) -> BatchFeature:
        """Декодирует изображения и подготавливает тензоры с заданным бюджетом пикселей."""
        contents = [[{"type": "image"}] for task in self.tasks]
        prompts = self.prompts(processor, config.instruction, contents)
        with ExitStack() as stack:
            images = [stack.enter_context(task.image.open()) for task in self.tasks]
            return processor(
                text=prompts, images=images, padding=True, truncation=False,
                return_tensors="pt", min_pixels=4096, max_pixels=config.max_image_pixels,
            )


def validate_vectors(value: object, expected_count: int) -> list[list[float]]:
    """Проверяет число, размерность, конечность и нормализацию векторов."""
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
