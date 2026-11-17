"""Хранение и декодирование изображения в памяти."""

import base64
import binascii
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps

from validation import TaskError


@dataclass(frozen=True)
class EncodedImage:
    data: bytes

    @classmethod
    def from_base64(cls, value: str) -> "EncodedImage":
        """Декодирует base64 и проверяет изображение без хранения копии пикселей."""
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise TaskError("image: ожидается стандартная base64 без data URI") from exc
        image = cls(data)
        with image.open():
            pass
        return image

    def open(self) -> Image.Image:
        """Декодирует неподвижное изображение в RGB с учётом EXIF-ориентации."""
        try:
            with Image.open(BytesIO(self.data)) as source:
                if getattr(source, "n_frames", 1) != 1:
                    raise TaskError("Одна задача должна содержать одно неподвижное изображение")
                source.load()
                return ImageOps.exif_transpose(source).convert("RGB")
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise TaskError("image: невозможно декодировать изображение") from exc
