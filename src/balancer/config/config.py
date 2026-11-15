"""Загрузка и проверка конфигурации без запуска процессов и создания каталогов."""
import __main__
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from src.balancer.config.validation import ConfigError, _fields, _number, CoreUrl, Token

assert __main__.__file__ is not None, "Неправильный запуск"
MAIN_DIR = Path(__main__.__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    core_url: CoreUrl# Адрес ядра
    token: Token = field(repr=False) # Токен доступа балансировщика к core
    delay: float # Минимальная задержка между началом циклов получения задач
    check_delay: float # Задержка между циклами получения проверки
    retry_delay: float # Задержка мжеду сетевыми запросами
    http_timeout: float # Таймаут http
    temp_dir: Path = MAIN_DIR / "tmp"

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Собрать конфиг"""
        _fields(data,
                {"core_url", "token", "delay", "check_delay", "retry_delay", "http_timeout", "temp_dir"},
                "config"
                )
        temp_dir = data["temp_dir"]
        if not isinstance(temp_dir, str) or not temp_dir.strip() or "\0" in temp_dir:
            raise ConfigError("temp_dir: ожидается непустой путь без NUL")

        return cls(
            core_url=CoreUrl.from_string(data["core_url"]),
            token=Token(data["token"]),
            delay=_number(data["delay"], "delay"),
            check_delay=_number(data["check_delay"], "check_delay"),
            retry_delay=_number(data["retry_delay"], "retry_delay", positive=True),
            http_timeout=_number(data["http_timeout"], "http_timeout", positive=True),
        )

    @classmethod
    def from_yaml(cls, path: str | Path = MAIN_DIR / "config" / "config.yaml") -> "Config":
        """Прочитать YAML"""
        try:
            config_path = Path(path).absolute()
            with config_path.open(encoding="utf-8") as stream:
                data = yaml.safe_load(stream)
        except (OSError, UnicodeError, yaml.YAMLError, ValueError, RecursionError):
            # Ошибки YAML включают исходные строки, в которых может быть token.
            raise ConfigError("Не удалось прочитать конфигурацию YAML") from None
        return cls.from_dict(data)

