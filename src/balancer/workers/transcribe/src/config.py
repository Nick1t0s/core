"""Конфигурация принадлежит воркеру и не загружается балансировщиком."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from validation import ConfigError, _fields, _string


@dataclass(frozen=True)
class Config:
    model: str
    device: str
    compute_type: str
    language: str | None

    @classmethod
    def from_dict(cls, data: object) -> "Config":
        fields = _fields(data, {"model", "device", "compute_type", "language"}, "config", ConfigError)
        model = _string(fields["model"], "model", ConfigError)
        device = _string(fields["device"], "device", ConfigError)
        compute_type = _string(fields["compute_type"], "compute_type", ConfigError)
        language = fields["language"]
        if device not in {"cuda", "cpu", "auto"}:
            raise ConfigError("device: ожидается cuda, cpu или auto")
        if language is not None and (
            not isinstance(language, str) or not language.strip()
        ):
            raise ConfigError("language: ожидается код языка или null")
        return cls(model=model, device=device, compute_type=compute_type, language=language)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        try:
            with Path(path).open(encoding="utf-8") as stream:
                data: object = yaml.safe_load(stream)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ConfigError("Не удалось прочитать YAML-конфиг transcribe") from exc
        return cls.from_dict(data)
