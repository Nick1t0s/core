"""Разбор, проверка и транскрибация пачки задач."""

from contextlib import redirect_stdout
from dataclasses import field
from functools import partial
import json
import sys
from typing import Annotated, TYPE_CHECKING, TextIO

from pydantic import Field, TypeAdapter, ValidationError, field_validator
from pydantic.dataclasses import dataclass

from audio import Audio
from config import Config
from validation import BatchError, NonEmptyString, TaskError, VALIDATION_CONFIG, unique_object, invalid_constant

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


@dataclass(config=VALIDATION_CONFIG)
class Task:
    task_id: NonEmptyString
    audio: Audio
    text: str | None = field(default=None, init=False)

    @field_validator("audio", mode="before")
    @classmethod
    def decode_audio(cls, value: object) -> object:
        """Преобразует входную base64-строку в объект аудио."""
        if isinstance(value, str):
            return Audio.from_base64(value)
        return value

    @classmethod
    def from_dict(cls, data: object) -> "Task":
        """Проверяет поля задачи и создаёт объект аудио из base64."""
        try:
            return TypeAdapter(cls).validate_python(data)
        except ValidationError as exc:
            raise TaskError(str(exc)) from exc

    def transcribe(self, model: "WhisperModel", language: str | None) -> None:
        """Сохранить транскрипцию с таймкодами начала сегментов."""
        self.text = None
        with redirect_stdout(sys.stderr), self.audio.open() as audio:
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


@dataclass(config=VALIDATION_CONFIG)
class Batch:
    tasks: Annotated[tuple[Task, ...], Field(min_length=1)]
    results: dict[str, str | None] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Проверяет уникальность идентификаторов после проверки всех задач."""
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise BatchError("task_id должен быть уникальным в пачке")

    @classmethod
    def from_list(cls, data: object) -> "Batch":
        """Создаёт пачку с автоматической проверкой списка и вложенных задач."""
        try:
            return TypeAdapter(cls).validate_python({"tasks": data})
        except ValidationError as exc:
            raise BatchError(str(exc)) from exc

    @classmethod
    def from_json(cls, stream: TextIO) -> "Batch":
        """Читает JSON-массив без повторных ключей и создаёт проверенную пачку."""
        try:
            data: object = json.load(
                stream,
                object_pairs_hook=partial(unique_object, error=BatchError),
                parse_constant=partial(invalid_constant, error=BatchError),
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
