"""Конфигурация принадлежит воркеру и не загружается балансировщиком."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Config:
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        if not isinstance(data, dict) or data.keys() - {"model", "device", "compute_type", "language"}:
            raise ValueError("Ожидается словарь параметров transcribe без неизвестных полей")
        config = cls(**data)
        for name in ("model", "device", "compute_type"):
            value = getattr(config, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}: ожидается непустая строка")
        if config.device not in {"cuda", "cpu", "auto"}:
            raise ValueError("device: ожидается cuda, cpu или auto")
        if config.language is not None and (
            not isinstance(config.language, str) or not config.language.strip()
        ):
            raise ValueError("language: ожидается код языка или null")
        return config

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        try:
            with Path(path).open(encoding="utf-8") as stream:
                data = yaml.safe_load(stream)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ValueError("Не удалось прочитать YAML-конфиг transcribe") from exc
        return cls.from_dict(data)
