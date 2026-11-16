"""Ошибки и общие проверки входных данных воркера."""

from typing import cast


class ConfigError(ValueError):
    """Конфигурацию невозможно прочитать или её параметры недопустимы."""


class TaskError(ValueError):
    """Поля задачи или путь к входному файлу недопустимы."""


class BatchError(ValueError):
    """Пачку невозможно прочитать или её состав недопустим."""


def _fields(
    value: object,
    expected: set[str],
    location: str,
    error_type: type[ValueError],
) -> dict[str, object]:
    """Проверить наличие всех ключей и отсутствие неизвестных."""
    if not isinstance(value, dict):
        raise error_type(f"{location}: ожидается объект с параметрами")
    if value.keys() != expected:
        raise error_type(f"{location}: отсутствуют обязательные или заданы неизвестные параметры")
    return cast(dict[str, object], value)


def _string(value: object, location: str, error_type: type[ValueError]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{location}: ожидается непустая строка")
    return value
