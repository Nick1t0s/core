"""Ошибки и общие проверки входных данных воркера."""


class ConfigError(ValueError):
    """Конфигурацию невозможно прочитать или её параметры недопустимы."""


class TaskError(ValueError):
    """Поля задачи или путь к входному файлу недопустимы."""


class BatchError(ValueError):
    """Пачку невозможно прочитать или её состав недопустим."""


def _fields(value, expected, location, error_type):
    """Проверить наличие всех ключей и отсутствие неизвестных."""
    if not isinstance(value, dict):
        raise error_type(f"{location}: ожидается объект с параметрами")
    if value.keys() != expected:
        raise error_type(f"{location}: отсутствуют обязательные или заданы неизвестные параметры")
