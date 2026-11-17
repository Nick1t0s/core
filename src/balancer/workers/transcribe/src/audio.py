"""Хранение закодированного аудиофайла и открытие потока в памяти."""

import base64
import binascii
from io import BytesIO
from typing import Annotated

from pydantic import Field
from pydantic.dataclasses import dataclass

from validation import TaskError, VALIDATION_CONFIG


@dataclass(frozen=True, config=VALIDATION_CONFIG)
class Audio:
    data: Annotated[bytes, Field(strict=True, min_length=1)]

    @classmethod
    def from_base64(cls, value: str) -> "Audio":
        """Декодирует стандартную base64 в байты аудиофайла."""
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise TaskError("audio: ожидается стандартная base64 без data URI") from exc
        return cls(data)

    def open(self) -> BytesIO:
        """Открывает новый поток аудиобайтов для чтения распознавателем."""
        return BytesIO(self.data)
