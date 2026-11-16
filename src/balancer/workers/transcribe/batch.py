"""Разбор, проверка и транскрибация пачки задач."""

from contextlib import redirect_stdout
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, TextIO

from config import Config
from validation import BatchError, TaskError, _fields

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


@dataclass(frozen=True)
class Task:
    task_id: str
    path: Path

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id:
            raise TaskError("task_id должен быть непустой строкой")
        if not isinstance(self.path, Path) or not self.path.is_absolute() or not self.path.is_file():
            raise TaskError("path должен указывать на существующий абсолютный путь к файлу")

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        _fields(data, {"task_id", "path"}, "task", TaskError)
        if not isinstance(data["path"], str):
            raise TaskError("path должен быть строкой")
        return cls(task_id=data["task_id"], path=Path(data["path"]))

    def transcribe(self, model: "WhisperModel", language: str | None) -> dict:
        """Распознать файл с уже загруженной моделью."""
        with redirect_stdout(sys.stderr):
            segments, _ = model.transcribe(str(self.path), language=language)
            text = "".join(segment.text for segment in segments).strip()
        return {"task_id": self.task_id, "payload": {"text": text}}


@dataclass(frozen=True)
class Batch:
    tasks: tuple[Task, ...]

    def __post_init__(self):
        if not isinstance(self.tasks, tuple) or not self.tasks:
            raise BatchError("Пачка должна содержать непустой кортеж задач")
        if any(not isinstance(task, Task) for task in self.tasks):
            raise BatchError("Пачка должна содержать объекты Task")
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise BatchError("task_id должен быть уникальным в пачке")

    @classmethod
    def from_list(cls, data: list) -> "Batch":
        if not isinstance(data, list) or not data:
            raise BatchError("Ожидается непустой массив задач")
        return cls(tasks=tuple(Task.from_dict(item) for item in data))

    @classmethod
    def from_json(cls, stream: TextIO) -> "Batch":
        try:
            data = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BatchError("Не удалось прочитать JSON-пачку") from exc
        return cls.from_list(data)

    def transcribe(self, config: Config) -> list[dict]:
        """Загрузить модель один раз и последовательно распознать всю пачку."""
        with redirect_stdout(sys.stderr):
            from faster_whisper import WhisperModel

            model = WhisperModel(
                config.model,
                device=config.device,
                compute_type=config.compute_type,
                local_files_only=False,
            )
            return [task.transcribe(model, config.language) for task in self.tasks]
