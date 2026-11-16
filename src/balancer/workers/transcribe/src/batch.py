"""Разбор, проверка и транскрибация пачки задач."""

from contextlib import redirect_stdout
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, TextIO, cast

from config import Config
from validation import BatchError, TaskError, _fields

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


@dataclass
class Task:
    task_id: str
    path: Path
    text: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id:
            raise TaskError("task_id должен быть непустой строкой")
        if not isinstance(self.path, Path) or not self.path.is_absolute() or not self.path.is_file():
            raise TaskError("path должен указывать на существующий абсолютный путь к файлу")

    @classmethod
    def from_dict(cls, data: object) -> "Task":
        fields = _fields(data, {"task_id", "path"}, "task", TaskError)
        task_id = fields["task_id"]
        path = fields["path"]
        if not isinstance(task_id, str) or not task_id:
            raise TaskError("task_id должен быть непустой строкой")
        if not isinstance(path, str):
            raise TaskError("path должен быть строкой")
        return cls(task_id=task_id, path=Path(path))

    def transcribe(self, model: "WhisperModel", language: str | None) -> None:
        """Сохранить транскрипцию с таймкодами начала сегментов."""
        self.text = None
        with redirect_stdout(sys.stderr):
            segments, _ = model.transcribe(str(self.path), language=language)
            lines: list[str] = []
            for segment in segments:
                text = " ".join(segment.text.split())
                if not text:
                    continue
                hours, remainder = divmod(int(segment.start), 3600)
                minutes, seconds = divmod(remainder, 60)
                lines.append(f"[{hours:02d}:{minutes:02d}:{seconds:02d}] {text}")
        self.text = "\n".join(lines)


@dataclass
class Batch:
    tasks: tuple[Task, ...]
    results: dict[str, str | None] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.tasks, tuple) or not self.tasks:
            raise BatchError("Пачка должна содержать непустой кортеж задач")
        if any(not isinstance(task, Task) for task in self.tasks):
            raise BatchError("Пачка должна содержать объекты Task")
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise BatchError("task_id должен быть уникальным в пачке")

    @classmethod
    def from_list(cls, data: object) -> "Batch":
        if not isinstance(data, list) or not data:
            raise BatchError("Ожидается непустой массив задач")
        items = cast(list[object], data)
        return cls(tasks=tuple(Task.from_dict(item) for item in items))

    @classmethod
    def from_json(cls, stream: TextIO) -> "Batch":
        try:
            data: object = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BatchError("Не удалось прочитать JSON-пачку") from exc
        return cls.from_list(data)

    def transcribe(self, config: Config) -> None:
        """Загрузить модель один раз и последовательно распознать всю пачку."""
        self.results = None
        for task in self.tasks:
            task.text = None
        with redirect_stdout(sys.stderr):
            from faster_whisper import WhisperModel

            model = WhisperModel(
                config.model,
                device=config.device,
                compute_type=config.compute_type,
                local_files_only=False,
            )
            for task in self.tasks:
                task.transcribe(model, config.language)
        self.results = {task.task_id: task.text for task in self.tasks}

    def write(self) -> None:
        """Вывести JSON только после успешной обработки всей пачки."""
        print(json.dumps(self.results, ensure_ascii=False))
