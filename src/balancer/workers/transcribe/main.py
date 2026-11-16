"""Транскрибация локальных аудиофайлов через faster-whisper."""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
from config import Config


def read_tasks(stream) -> list[dict]:
    """Проверить всю пачку до загрузки модели и обработки файлов."""
    tasks = json.load(stream)
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Ожидается непустой массив задач")
    task_ids = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {"task_id", "path"}:
            raise ValueError("У задачи ожидаются поля task_id и path")
        task_id = task["task_id"]
        if not isinstance(task_id, str) or not task_id or task_id in task_ids:
            raise ValueError("task_id должен быть непустой уникальной строкой")
        task_ids.add(task_id)
        path = task["path"]
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_file():
            raise ValueError("path должен указывать на существующий абсолютный путь к файлу")
    return tasks


def transcribe(tasks: list[dict], config: Config) -> list[dict]:
    # Импорт и библиотечный вывод также не должны попадать в JSON на stdout.
    with redirect_stdout(sys.stderr):
        from faster_whisper import WhisperModel

        model = WhisperModel(
            config.model,
            device=config.device,
            compute_type=config.compute_type,
            local_files_only=False,
        )
        results = []
        for task in tasks:
            segments, _ = model.transcribe(task["path"], language=config.language)
            text = "".join(segment.text for segment in segments).strip()
            results.append({"task_id": task["task_id"], "payload": {"text": text}})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="YAML-конфиг воркера")
    args = parser.parse_args(argv)

    try:
        config = Config.from_yaml(args.config)
        tasks = read_tasks(sys.stdin)
        results = transcribe(tasks, config)
        # При ошибке любой задачи ни один частичный результат не публикуется.
        print(json.dumps({"results": results}, ensure_ascii=False))
    except Exception as exc:
        print(f"transcribe: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
