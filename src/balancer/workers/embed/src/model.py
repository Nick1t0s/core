"""Qwen3-VL: общий трансформер, last-token pooling и L2-нормализация."""

from contextlib import ExitStack

from PIL import Image
import torch
from transformers import Qwen3VLForConditionalGeneration, Qwen3VLModel, Qwen3VLProcessor

from batch import Task, validate_vectors
from config import Config, DIMENSIONS
from validation import BatchError, ConfigError


class QwenEmbedder:
    def __init__(self, config: Config) -> None:
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

    def embed(self, tasks: tuple[Task, ...]) -> list[list[float]]:
        with ExitStack() as stack:
            conversations: list[list[dict[str, object]]] = []
            images: list[Image.Image] = []
            for task in tasks:
                content: list[dict[str, str]]
                if task.text is not None:
                    content = [{"type": "text", "text": task.text}]
                else:
                    images.append(stack.enter_context(task.open_image()))
                    content = [{"type": "image"}]
                conversations.append([
                    {"role": "system", "content": [{"type": "text", "text": self.config.instruction}]},
                    {"role": "user", "content": content},
                ])
            prompts: object = self.processor.apply_chat_template(
                conversations, tokenize=False, add_generation_prompt=True,
            )
            if not isinstance(prompts, list) or not all(isinstance(item, str) for item in prompts):
                raise BatchError("Процессор вернул неверный формат промптов")
            if images:
                processed = self.processor(
                    text=prompts, images=images, padding=True, truncation=False,
                    return_tensors="pt", min_pixels=4096, max_pixels=self.config.max_image_pixels,
                )
            else:
                processed = self.processor(
                    text=prompts, padding=True, truncation=False, return_tensors="pt",
                )
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
            rows = torch.arange(len(tasks), device=hidden.device)
            pooled = hidden[rows, positions].float()
            normalized = torch.nn.functional.normalize(pooled, p=2, dim=-1)
            vectors: object = normalized.cpu().tolist()
        return validate_vectors(vectors, len(tasks))
