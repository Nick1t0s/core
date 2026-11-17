"""Проверенный вектор одной задачи."""

import math
from typing import Annotated

from pydantic import Field
from pydantic.dataclasses import dataclass

from config import DIMENSIONS
from validation import BatchError, VALIDATION_CONFIG

FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Vector = Annotated[list[FiniteNumber], Field(min_length=DIMENSIONS, max_length=DIMENSIONS)]


@dataclass(config=VALIDATION_CONFIG)
class Embedding:
    vector: Vector

    def __post_init__(self) -> None:
        """Проверяет единичную L2-норму вектора."""
        if not math.isclose(math.hypot(*self.vector), 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise BatchError("Эмбеддинг должен быть нормализован")
