"""Конфигурация принадлежит воркеру и не загружается балансировщиком."""

from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass
import yaml

from validation import ConfigError, NonEmptyString, VALIDATION_CONFIG


@dataclass(frozen=True, config=VALIDATION_CONFIG)
class Config:
    model: NonEmptyString
    device: Literal["cuda", "cpu", "auto"]
    compute_type: NonEmptyString
    language: NonEmptyString | None

    @classmethod
    def from_dict(cls, data: object) -> "Config":
        """Создаёт конфигурацию с автоматической проверкой полей Pydantic."""
        try:
            return TypeAdapter(cls).validate_python(data)
        except ValidationError as exc:
            raise ConfigError(str(exc)) from exc

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        """Читает YAML-файл и создаёт проверенную конфигурацию."""
        try:
            with Path(path).open(encoding="utf-8") as stream:
                data: object = yaml.safe_load(stream)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ConfigError("Не удалось прочитать YAML-конфиг transcribe") from exc
        return cls.from_dict(data)
