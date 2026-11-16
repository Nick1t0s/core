"""Транскрибация локальных аудиофайлов через faster-whisper."""

import argparse
from pathlib import Path
import sys
from config import Config
from batch import Batch


class Arguments(argparse.Namespace):
    config: Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="YAML-конфиг воркера")
    args = parser.parse_args(argv, namespace=Arguments())

    try:
        config = Config.from_yaml(args.config)
        batch = Batch.from_json(sys.stdin)
        batch.transcribe(config)
        batch.write()
    except Exception as exc:
        print(f"transcribe: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
