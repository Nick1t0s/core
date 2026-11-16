"""Ошибки и проверка внешних данных воркера эмбеддингов."""

from typing import cast


class ConfigError(ValueError):
    """Неверная конфигурация воркера."""


class TaskError(ValueError):
    """Неверные входные данные задачи."""


class BatchError(ValueError):
    """Неверная пачка или результат вычислений."""


def fields(value: object, expected: set[str], error: type[ValueError]) -> dict[str, object]:
    """Проверяет, что передан словарь с ровно указанным набором полей."""
    if not isinstance(value, dict) or value.keys() != expected:
        raise error("Отсутствуют обязательные или заданы неизвестные поля")
    return cast(dict[str, object], value)


def nonempty_string(value: object, name: str, error: type[ValueError]) -> str:
    """Проверяет непустую строку и возвращает её без изменения пробелов."""
    if not isinstance(value, str) or not value.strip():
        raise error(f"{name}: ожидается непустая строка")
    return value


def positive_integer(value: object, name: str, error: type[ValueError]) -> int:
    """Проверяет положительное целое число, исключая булевы значения."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise error(f"{name}: ожидается положительное целое число")
    return value


def unique_object(pairs: list[tuple[str, object]], error: type[ValueError]) -> dict[str, object]:
    """Собирает JSON-объект из пар, отклоняя повторные ключи."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise error("Повторный ключ в JSON")
        result[key] = value
    return result


def invalid_constant(value: str, error: type[ValueError]) -> object:
    """Отклоняет нестандартные JSON-константы NaN и Infinity."""
    raise error(f"Недопустимая JSON-константа: {value}")
