"""Типы полей, ошибки и строгий разбор JSON воркера."""

from typing import Annotated

from pydantic import ConfigDict, StringConstraints


VALIDATION_CONFIG = ConfigDict(extra="forbid", hide_input_in_errors=True)
NonEmptyString = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]


class ConfigError(ValueError):
    """Неверная конфигурация воркера."""


class TaskError(ValueError):
    """Неверные входные данные задачи."""


class BatchError(ValueError):
    """Неверная пачка или результат обработки."""


def unique_object(pairs: list[tuple[str, object]], error: type[ValueError]) -> dict[str, object]:
    """Собирает JSON-объект, отклоняя повторные ключи до проверки моделей."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise error("Повторный ключ в JSON")
        result[key] = value
    return result


def invalid_constant(value: str, error: type[ValueError]) -> object:
    """Отклоняет нестандартные JSON-константы NaN и Infinity."""
    raise error(f"Недопустимая JSON-константа: {value}")
