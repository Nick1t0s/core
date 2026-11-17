"""Qwen3-VL: общий трансформер, last-token pooling и L2-нормализация."""

import torch
from transformers import Qwen3VLForConditionalGeneration, Qwen3VLModel, Qwen3VLProcessor

from config import Config, DIMENSIONS
from inference import InferenceBatch, validate_vectors
from task import Task
from validation import BatchError, ConfigError


class QwenEmbedder:
    def __init__(self, config: Config) -> None:
        """Проверяет устройство и загружает локальные веса и процессор модели."""
        self.config = config
        if config.device == "cuda":
            if not torch.cuda.is_available():
                raise ConfigError("CUDA недоступна")
            if config.compute_type == "bfloat16" and not torch.cuda.is_bf16_supported():
                raise ConfigError("GPU не поддерживает BF16; задайте compute_type: float16")
        dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}[config.compute_type]
        loaded: object = Qwen3VLForConditionalGeneration.from_pretrained(
            config.model, dtype=dtype, attn_implementation="sdpa", local_files_only=True,
            output_loading_info=True,
        )
        processor: object = Qwen3VLProcessor.from_pretrained(config.model, local_files_only=True)
        if not isinstance(loaded, tuple) or len(loaded) != 2:
            raise ConfigError("Не удалось загрузить Qwen3-VL")
        model, loading_info = loaded
        if not isinstance(model, Qwen3VLForConditionalGeneration) or not isinstance(processor, Qwen3VLProcessor):
            raise ConfigError("Не удалось загрузить Qwen3-VL")
        if not isinstance(loading_info, dict) or any(
            loading_info.get(key) for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")
        ):
            raise ConfigError("Веса модели неполны или несовместимы с Qwen3-VL")
        if model.config.text_config.hidden_size != DIMENSIONS:
            raise ConfigError("Модель должна выдавать векторы размерности 2048")
        self.model: Qwen3VLModel = model.model
        torch.nn.Module.to(self.model, device=torch.device(config.device))
        torch.nn.Module.eval(self.model)
        self.processor = processor

    def embed(self, batch: InferenceBatch[Task]) -> list[list[float]]:
        """Подготавливает входы и вычисляет нормализованные векторы последних токенов."""
        processed = batch.prepare(self.processor, self.config)
        raw: object = processed.data
        if not isinstance(raw, dict):
            raise BatchError("Процессор вернул неверный формат входов")
        inputs: dict[str, torch.Tensor] = {}
        for name, tensor in raw.items():
            if not isinstance(name, str) or not isinstance(tensor, torch.Tensor):
                raise BatchError("Процессор должен возвращать тензоры")
            inputs[name] = tensor
        input_ids = inputs["input_ids"]
        if input_ids.shape[1] > self.config.max_tokens:
            raise BatchError("Вход превышает max_tokens; текст не обрезается")
        with torch.inference_mode():
            inputs = {name: tensor.to(self.config.device) for name, tensor in inputs.items()}
            outputs = self.model(**inputs, use_cache=False, return_dict=True)
            hidden: object = outputs.last_hidden_state
            if not isinstance(hidden, torch.Tensor):
                raise BatchError("Модель не вернула скрытые представления")
            mask = inputs["attention_mask"]
            # Работает при padding как слева, так и справа.
            positions = mask.shape[1] - 1 - mask.flip(dims=[1]).argmax(dim=1)
            rows = torch.arange(len(batch.tasks), device=hidden.device)
            pooled = hidden[rows, positions].float()
            normalized = torch.nn.functional.normalize(pooled, p=2, dim=-1)
            vectors: object = normalized.cpu().tolist()
        return validate_vectors(vectors, len(batch.tasks))
