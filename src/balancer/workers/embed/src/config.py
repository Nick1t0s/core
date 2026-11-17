"""Обязательная конфигурация одного запуска embed."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass
import yaml

from validation import ConfigError, NonEmptyString, VALIDATION_CONFIG

MODEL_ID = "Qwen/Qwen3-VL-Embedding-2B"
MODEL_NAME = "Qwen3-VL-Embedding-2B"
DIMENSIONS = 2048
MAX_TEXT_CHARACTERS = 2000


@dataclass(frozen=True, config=VALIDATION_CONFIG)
class Config:
    model: Literal["Qwen/Qwen3-VL-Embedding-2B"]
    device: Literal["cuda", "cpu"]
    compute_type: Literal["bfloat16", "float16", "float32"]
    max_tokens: Annotated[int, Field(strict=True, gt=0, le=32768)]
    max_image_pixels: Annotated[int, Field(strict=True, ge=4096, multiple_of=1024)]
    instruction: NonEmptyString

    def __post_init__(self) -> None:
        """Проверяет совместимость устройства и типа вычислений."""
        if self.device == "cpu" and self.compute_type != "float32":
            raise ConfigError("Для CPU требуется compute_type: float32")

    @classmethod
    def from_dict(cls, value: object) -> "Config":
        """Создаёт конфигурацию с автоматической проверкой полей Pydantic."""
        try:
            return TypeAdapter(cls).validate_python(value)
        except ValidationError as exc:
            raise ConfigError(str(exc)) from exc

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        """Читает YAML-файл и создаёт проверенную конфигурацию."""
        try:
            with Path(path).open(encoding="utf-8") as stream:
                data: object = yaml.safe_load(stream)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ConfigError("Не удалось прочитать YAML-конфиг embed") from exc
        return cls.from_dict(data)
