"""Проверки параметров конфигурации воркера."""


class ConfigError(ValueError):
    """Конфигурацию невозможно прочитать или её параметры недопустимы."""


def _fields(value, expected, location):
    """Проверить наличие всех ключей и отсутствие неизвестных."""
    if not isinstance(value, dict):
        raise ConfigError(f"{location}: ожидается объект с параметрами")
    if value.keys() != expected:
        raise ConfigError(f"{location}: отсутствуют обязательные или заданы неизвестные параметры")
