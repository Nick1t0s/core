"""Транскрибация локальных аудиофайлов через faster-whisper."""

import argparse
import json
from pathlib import Path
import sys
from config import Config
from batch import Batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="YAML-конфиг воркера")
    args = parser.parse_args(argv)

    try:
        config = Config.from_yaml(args.config)
        batch = Batch.from_json(sys.stdin)
        results = batch.transcribe(config)
        # При ошибке любой задачи ни один частичный результат не публикуется.
        print(json.dumps({"results": results}, ensure_ascii=False))
    except Exception as exc:
        print(f"transcribe: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
