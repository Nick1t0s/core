"""Текстовые и графические задачи с общим идентификатором."""

from dataclasses import dataclass

from config import MAX_TEXT_CHARACTERS
from image import EncodedImage
from validation import TaskError, fields, nonempty_string


@dataclass(frozen=True)
class Task:
    task_id: str


@dataclass(frozen=True)
class TextTask(Task):
    text: str


@dataclass(frozen=True)
class ImageTask(Task):
    image: EncodedImage


def task_from_dict(value: object) -> TextTask | ImageTask:
    """Проверяет входные поля и создаёт текстовую или графическую задачу."""
    if not isinstance(value, dict):
        raise TaskError("Задача должна быть JSON-объектом")
    content = "text" if "text" in value else "image"
    data = fields(value, {"task_id", content}, TaskError)
    task_id = nonempty_string(data["task_id"], "task_id", TaskError)
    source = nonempty_string(data[content], content, TaskError)
    if content == "text":
        if len(source) > MAX_TEXT_CHARACTERS:
            raise TaskError("text: максимум 2000 символов, включая пробелы")
        try:
            source.encode("utf-8")
        except UnicodeError as exc:
            raise TaskError("text: некорректный Unicode") from exc
        return TextTask(task_id, source)
    return ImageTask(task_id, EncodedImage.from_base64(source))
