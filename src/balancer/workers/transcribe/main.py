"""Транскрибация локальных аудиофайлов через faster-whisper."""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
from config import Config


def read_tasks(stream) -> list[dict]:
    """Проверить всю пачку до загрузки модели и обработки файлов."""
    data = json.load(stream)
    if not isinstance(data, dict) or set(data) != {"type", "tasks"}:
        raise ValueError("Ожидаются поля type и tasks")
    if data["type"] != "transcribe":
        raise ValueError("Ожидается type=transcribe")
    tasks = data["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks должен быть непустым массивом")
    task_ids = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {"task_id", "input"}:
            raise ValueError("У задачи ожидаются поля task_id и input")
        task_id = task["task_id"]
        if not isinstance(task_id, str) or not task_id or task_id in task_ids:
            raise ValueError("task_id должен быть непустой уникальной строкой")
        task_ids.add(task_id)
        source = task["input"]
        if not isinstance(source, dict) or set(source) != {"path", "mime"}:
            raise ValueError("У input ожидаются поля path и mime")
        path = source["path"]
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_file():
            raise ValueError("input.path должен указывать на существующий абсолютный путь к файлу")
        if not isinstance(source["mime"], str) or not source["mime"].strip():
            raise ValueError("input.mime должен быть непустой строкой")
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
            segments, _ = model.transcribe(task["input"]["path"], language=config.language)
            text = "".join(segment.text for segment in segments).strip()
            results.append({"task_id": task["task_id"], "payload": {"text": text}})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="YAML-конфиг воркера")
    parser.add_argument("--file", type=Path, help="Один аудиофайл вместо JSON-пачки на stdin")
    args = parser.parse_args(argv)

    try:
        config = Config.from_yaml(args.config) if args.config else Config()
        if args.file is not None:
            if not args.file.is_file():
                raise ValueError("--file должен указывать на существующий файл")
            tasks = [{"task_id": "file", "input": {"path": str(args.file.resolve())}}]
        else:
            tasks = read_tasks(sys.stdin)
        results = transcribe(tasks, config)
        # При ошибке любой задачи ни один частичный результат не публикуется.
        if args.file is not None:
            print(results[0]["payload"]["text"])
        else:
            print(json.dumps({"results": results}, ensure_ascii=False))
    except Exception as exc:
        print(f"transcribe: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
