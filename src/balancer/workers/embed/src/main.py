"""Эмбеддинги текстов и base64-изображений из одной JSON-пачки."""

import argparse
from contextlib import contextmanager, redirect_stdout
import os
from pathlib import Path
import sys
from typing import Iterator


class Arguments(argparse.Namespace):
    config: Path


@contextmanager
def diagnostics_to_stderr() -> Iterator[None]:
    """Убрать из stdout также нативный вывод вычислительных библиотек."""
    sys.stdout.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        with redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stderr.flush()
        os.dup2(saved, 1)
        os.close(saved)


def main(argv: list[str] | None = None) -> int:
    """Читает конфиг и пачку, вычисляет эмбеддинги и выводит результат или ошибку."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="YAML-конфиг воркера")
    args = parser.parse_args(argv, namespace=Arguments())
    try:
        with diagnostics_to_stderr():
            from config import Config
            from batch import Batch

            config = Config.from_yaml(args.config)
            batch = Batch.from_json(sys.stdin)
            from model import QwenEmbedder

            batch.embed(config, QwenEmbedder(config))
        batch.write(sys.stdout)
    except Exception as exc:
        print(f"embed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
