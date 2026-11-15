"""Общие проверки параметров конфигурации."""

import math
from dataclasses import dataclass
from yarl import URL

class ConfigError(ValueError):
    """Конфигурацию невозможно прочитать или её параметры недопустимы."""


def _fields(value, expected, location):
    """Проверка словаря на наличие всех ключей"""
    if not isinstance(value, dict):
        raise ConfigError(f"{location}: ожидается объект с параметрами")
    # Не выводить неизвестные ключи: они тоже могут содержать секрет.
    if value.keys() != expected:
        raise ConfigError(f"{location}: отсутствуют обязательные или заданы неизвестные параметры")


def _number(value, location, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{location}: ожидается число")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ConfigError(f"{location}: ожидается конечное число") from None
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        requirement = "положительным" if positive else "неотрицательным"
        raise ConfigError(f"{location}: число должно быть конечным и {requirement}")
    return number

@dataclass(frozen=True)
class Token:
    token: str = field(repr=False)
    def __post_init__(self):
        if not self.token:
            raise ConfigError(f"Ожидается непустой токен")
        if any(not ('a' <= char <= 'z' or '0' <= char <= '9') for char in self.token):
            raise ConfigError(f"В токене могут присутствовать только цифры и строчные латинские буквы")

@dataclass(frozen=True)
class CoreUrl:
    url: URL
    @classmethod
    def from_string(cls, url: str):
        url_type: URL
        try:
            url_type = URL(url)
        except:
            raise ConfigError("URL core должен быть url")

        if not url_type.scheme in ("http", "https") or url_type.query:
            raise ConfigError("URL core должен иметь протокол http или https и не содержать параметров")

        return cls(url_type)