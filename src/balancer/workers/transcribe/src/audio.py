"""Хранение закодированного аудиофайла и открытие потока в памяти."""

import base64
import binascii
from dataclasses import dataclass
from io import BytesIO

from validation import TaskError


@dataclass(frozen=True)
class Audio:
    data: bytes

    def __post_init__(self) -> None:
        """Проверяет, что аудиофайл содержит непустые байты."""
        if not isinstance(self.data, bytes) or not self.data:
            raise TaskError("audio должен содержать непустые байты аудиофайла")

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
