"""Текстовые и графические задачи с проверкой при создании."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, TypeAdapter, ValidationError, field_validator
from pydantic.dataclasses import dataclass

from config import MAX_TEXT_CHARACTERS
from image import EncodedImage
from validation import NonEmptyString, TaskError, VALIDATION_CONFIG


@dataclass(frozen=True, config=VALIDATION_CONFIG)
class Task:
    task_id: NonEmptyString

    @classmethod
    def from_dict(cls, value: object) -> TextTask | ImageTask:
        """Создаёт задачу подходящего типа по набору её входных полей."""
        try:
            return TypeAdapter(TextTask | ImageTask).validate_python(value)
        except ValidationError as exc:
            raise TaskError(str(exc)) from exc


@dataclass(frozen=True, config=VALIDATION_CONFIG)
class TextTask(Task):
    text: Annotated[NonEmptyString, Field(max_length=MAX_TEXT_CHARACTERS)]

    def __post_init__(self) -> None:
        """Проверяет возможность передать исходный текст в UTF-8 без изменений."""
        try:
            self.text.encode("utf-8")
        except UnicodeError as exc:
            raise TaskError("text: некорректный Unicode") from exc


@dataclass(frozen=True, config=VALIDATION_CONFIG)
class ImageTask(Task):
    image: EncodedImage

    @field_validator("image", mode="before")
    @classmethod
    def decode_image(cls, value: object) -> object:
        """Преобразует входную base64-строку в проверенный объект изображения."""
        if isinstance(value, str):
            return EncodedImage.from_base64(value)
        return value
