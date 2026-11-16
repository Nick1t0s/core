"""Разбор, проверка и транскрибация пачки задач."""

from contextlib import redirect_stdout
from dataclasses import dataclass, field
import base64
import binascii
from io import BytesIO
import json
import sys
from typing import TYPE_CHECKING, TextIO, cast

from config import Config
from validation import BatchError, TaskError, _fields, _string, _unique_object, _invalid_constant

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


@dataclass
class Task:
    task_id: str
    audio: bytes
    text: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise TaskError("task_id должен быть непустой строкой")
        if not isinstance(self.audio, bytes) or not self.audio:
            raise TaskError("audio должен содержать непустые байты аудиофайла")

    @classmethod
    def from_dict(cls, data: object) -> "Task":
        fields = _fields(data, {"task_id", "audio"}, "task", TaskError)
        task_id = _string(fields["task_id"], "task_id", TaskError)
        encoded = _string(fields["audio"], "audio", TaskError)
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise TaskError("audio: ожидается стандартная base64 без data URI") from exc
        return cls(task_id=task_id, audio=audio)

    def transcribe(self, model: "WhisperModel", language: str | None) -> None:
        """Сохранить транскрипцию с таймкодами начала сегментов."""
        self.text = None
        with redirect_stdout(sys.stderr), BytesIO(self.audio) as audio:
            segments, _ = model.transcribe(audio, language=language)
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
            data: object = json.load(
                stream, object_pairs_hook=_unique_object, parse_constant=_invalid_constant,
            )
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
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
                local_files_only=True,
            )
            for task in self.tasks:
                task.transcribe(model, config.language)
        self.results = {task.task_id: task.text for task in self.tasks}

    def write(self) -> None:
        """Вывести JSON только после успешной обработки всей пачки."""
        print(json.dumps(self.results, ensure_ascii=False))
