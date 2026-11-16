"""Обязательная конфигурация одного запуска embed."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from validation import ConfigError, fields, nonempty_string, positive_integer

MODEL_ID = "Qwen/Qwen3-VL-Embedding-2B"
MODEL_NAME = "Qwen3-VL-Embedding-2B"
DIMENSIONS = 2048
MAX_TEXT_CHARACTERS = 2000


@dataclass(frozen=True)
class Config:
    model: str
    device: str
    compute_type: str
    text_batch_size: int
    image_batch_size: int
    max_tokens: int
    max_image_pixels: int
    instruction: str

    @classmethod
    def from_dict(cls, value: object) -> "Config":
        """Проверяет поля и значения настроек и создаёт конфигурацию."""
        data = fields(value, {
            "model", "device", "compute_type", "text_batch_size", "image_batch_size",
            "max_tokens", "max_image_pixels", "instruction",
        }, ConfigError)
        model = nonempty_string(data["model"], "model", ConfigError)
        device = nonempty_string(data["device"], "device", ConfigError)
        compute_type = nonempty_string(data["compute_type"], "compute_type", ConfigError)
        if model != MODEL_ID:
            raise ConfigError(f"model: поддерживается только {MODEL_ID}")
        if device not in {"cuda", "cpu"}:
            raise ConfigError("device: ожидается cuda или cpu")
        if compute_type not in {"bfloat16", "float16", "float32"}:
            raise ConfigError("compute_type: ожидается bfloat16, float16 или float32")
        if device == "cpu" and compute_type != "float32":
            raise ConfigError("Для CPU требуется compute_type: float32")
        max_tokens = positive_integer(data["max_tokens"], "max_tokens", ConfigError)
        max_pixels = positive_integer(data["max_image_pixels"], "max_image_pixels", ConfigError)
        if max_tokens > 32768:
            raise ConfigError("max_tokens не должен превышать 32768")
        if max_pixels < 4096 or max_pixels % 1024:
            raise ConfigError("max_image_pixels: требуется кратное 1024 число не меньше 4096")
        return cls(
            model, device, compute_type,
            positive_integer(data["text_batch_size"], "text_batch_size", ConfigError),
            positive_integer(data["image_batch_size"], "image_batch_size", ConfigError),
            max_tokens, max_pixels,
            nonempty_string(data["instruction"], "instruction", ConfigError),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        """Читает YAML-файл и создаёт проверенную конфигурацию."""
        try:
            with Path(path).open(encoding="utf-8") as stream:
                data: object = yaml.safe_load(stream)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ConfigError("Не удалось прочитать YAML-конфиг embed") from exc
        return cls.from_dict(data)
