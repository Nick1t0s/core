"""Конфигурация принадлежит воркеру и не загружается балансировщиком."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from validation import ConfigError, _fields


@dataclass(frozen=True)
class Config:
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        _fields(data, {"model", "device", "compute_type", "language"}, "config")
        config = cls(**data)
        for name in ("model", "device", "compute_type"):
            value = getattr(config, name)
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{name}: ожидается непустая строка")
        if config.device not in {"cuda", "cpu", "auto"}:
            raise ConfigError("device: ожидается cuda, cpu или auto")
        if config.language is not None and (
            not isinstance(config.language, str) or not config.language.strip()
        ):
            raise ConfigError("language: ожидается код языка или null")
        return config

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        try:
            with Path(path).open(encoding="utf-8") as stream:
                data = yaml.safe_load(stream)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ConfigError("Не удалось прочитать YAML-конфиг transcribe") from exc
        return cls.from_dict(data)
