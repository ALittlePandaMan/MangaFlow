"""Offline, single-model Transformers 5 process. Never imports the MangaOCR runtime."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import math
import sys
import types
import unicodedata
from pathlib import Path

from app.services.ocr.local_catalog import (
    LOCAL_OCR_DEFAULT_MAX_TOKENS,
    LOCAL_OCR_MAX_TOKENS,
    LOCAL_OCR_MODELS,
    verify_code,
)

PROTOCOL = "mangaflow-local-ocr-v1"


def send(**value):
    print(json.dumps({"protocol": PROTOCOL, **value}, ensure_ascii=False, allow_nan=False), flush=True)


def source_module(directory, name, *, package=None):
    qualified = f"{package}.{name}" if package else name
    spec = importlib.util.spec_from_file_location(qualified, directory / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


def checked_state(model, directory, *, tied=()):
    from safetensors.torch import load_file

    state = load_file(directory / "model.safetensors")
    # The verified checkpoints omit duplicate tied output embeddings.
    for target, owner in tied:
        if target not in state and owner in state:
            state[target] = state[owner]
    result = model.load_state_dict(state, strict=False, assign=True)
    if result.missing_keys or result.unexpected_keys:
        raise ValueError(f"OCR checkpoint mismatch: missing={result.missing_keys}, unexpected={result.unexpected_keys}")
    model.tie_weights()


def build_model(provider, directory, auxiliary, device):
    import torch
    from transformers import AutoProcessor

    # V100 has no native BF16. Float32 also avoids FP16 overflow in VL vision
    # layers; Ampere+ can use the checkpoint's native BF16 without conversion.
    dtype = torch.bfloat16 if device.startswith("cuda") and torch.cuda.get_device_capability(device)[0] >= 8 else torch.float32
    if provider == "paddleocr-vl-1.6":
        from transformers import PaddleOCRVLForConditionalGeneration

        model = PaddleOCRVLForConditionalGeneration.from_pretrained(directory, dtype=dtype,
            local_files_only=True, trust_remote_code=False, attn_implementation="eager").to(device).eval()
        processor = AutoProcessor.from_pretrained(directory, local_files_only=True, trust_remote_code=False)

        def recognize(image, maximum):
            messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": "OCR:"}]}]
            inputs = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=True,
                return_dict=True, return_tensors="pt")
            inputs = {key: value.to(device=device, dtype=dtype if value.is_floating_point() else value.dtype)
                      for key, value in inputs.items()}
            result = model.generate(**inputs, do_sample=False, max_new_tokens=maximum,
                                    repetition_penalty=1.2, use_cache=True)
            tokens = result[0, inputs["input_ids"].shape[-1]:]
            text = processor.decode(tokens, skip_special_tokens=True)
            eos = model.generation_config.eos_token_id
            eos = eos if isinstance(eos, list) else [eos]
            return text, len(tokens) >= maximum and int(tokens[-1]) not in eos

        return recognize

    verify_code(directory, LOCAL_OCR_MODELS[provider])
    if provider == "baberu-ocr":
        from transformers import AutoImageProcessor, LogitsProcessor, LogitsProcessorList

        config_module = source_module(directory, "configuration_baberu")
        module = source_module(directory, "modeling_baberu")
        tokenizer_module = source_module(directory, "tokenization_baberu")
        config = config_module.BaberuOCRConfig.from_pretrained(directory, local_files_only=True)
        config.vision_model_name = str(auxiliary)
        with torch.device("meta"):
            model = module.BaberuOCRModel(config)
        checked_state(model, directory, tied=(("lm_head.weight", "model.embed_tokens.weight"),))
        # This non-persistent buffer is absent from SafeTensors. Rebuild it
        # explicitly rather than materialize uninitialized RoPE frequencies.
        for layer in model.modules():
            if isinstance(layer, module.BaberuRotaryEmbedding):
                layer.inv_freq = 1. / (layer.theta ** (torch.arange(0, layer.dim, 2, dtype=torch.float32) / layer.dim))
        model.to(device=device, dtype=dtype).eval()
        tokenizer = tokenizer_module.BaberuTokenizer.from_pretrained(directory / "tokenizer", local_files_only=True)
        processor = AutoImageProcessor.from_pretrained(auxiliary, local_files_only=True, do_center_crop=False,
                                                       size={"height": 224, "width": 224}, crop_size=None)
        content = {i for i in range(len(tokenizer))
                   if len(ch := tokenizer.decode([i], skip_special_tokens=False)) == 1
                   and ch not in "ーｰ〜~" and unicodedata.category(ch)[0] in "LN"}

        class CapContentRun(LogitsProcessor):
            # Upstream's symbol-aware cap: do not truncate legitimate ellipses.
            def __call__(self, input_ids, scores):
                for b in range(len(input_ids)):
                    token = int(input_ids[b, -1])
                    if token in content and len(input_ids[b]) >= 12 and (input_ids[b, -12:] == token).all():
                        scores[b, token] = -float("inf")
                return scores

        def recognize(image, maximum):
            pixels = processor(image, return_tensors="pt")["pixel_values"].to(device=device, dtype=dtype)
            prefix = torch.tensor([[tokenizer.bos_token_id]], device=device)
            result = model.generate(input_ids=prefix, pixel_values=pixels, max_new_tokens=maximum,
                do_sample=False, repetition_penalty=1.2, logits_processor=LogitsProcessorList([CapContentRun()]),
                eos_token_id=tokenizer.eos_token_id, pad_token_id=tokenizer.pad_token_id,
                bos_token_id=tokenizer.bos_token_id, use_cache=True)
            tokens = result[0, 1:].cpu().tolist()
            return tokenizer.decode(tokens, skip_special_tokens=True), len(tokens) >= maximum and tokens[-1] != tokenizer.eos_token_id

        return recognize

    if provider != "hayai-ocr":
        raise ValueError("unknown local OCR model")
    from transformers import PreTrainedModel, PreTrainedTokenizerFast, Siglip2VisionConfig, Siglip2VisionModel

    package = types.ModuleType("mangaflow_hayai")
    package.__path__ = [str(directory)]
    sys.modules[package.__name__] = package
    configuration = source_module(directory, "configuration_hayai", package=package.__name__)
    module = source_module(directory, "modeling_hayai", package=package.__name__)

    class PinnedHayaiModel(module.HayaiModel):
        def __init__(self, config):
            # Same tree/order as pinned HayaiModel.__init__; replace its mutable
            # online Google config lookup with our pinned offline auxiliary.
            PreTrainedModel.__init__(self, config)
            self.vision_encoder = Siglip2VisionModel(Siglip2VisionConfig.from_pretrained(auxiliary, local_files_only=True))
            self.decoder = module.VisualCausalOCRDecoder(vocab_size=config.vocab_size, d_model=config.d_model,
                d_vision=config.d_vision, d_ffn=config.d_ffn, n_layers=config.n_layers)
            self.post_init()
            self.tie_weights()

    config = configuration.HayaiConfig.from_pretrained(directory, local_files_only=True)
    with torch.device("meta"):
        model = PinnedHayaiModel(config)
    checked_state(model, directory, tied=(("decoder.output_head.weight", "decoder.token_embeddings.weight"),))
    model.to(device=device, dtype=dtype).eval()
    tokenizer = PreTrainedTokenizerFast.from_pretrained(directory, local_files_only=True)
    processor = AutoProcessor.from_pretrained(auxiliary, local_files_only=True)

    def recognize(image, maximum):
        inputs = processor(images=[image], max_num_patches=256, return_tensors="pt").to(device)
        text, = model.generate(pixel_values=inputs["pixel_values"], pixel_attention_mask=inputs["pixel_attention_mask"],
            spatial_shapes=inputs["spatial_shapes"], tokenizer=tokenizer, max_new_tokens=maximum,
            num_beams=1, repetition_penalty=1.)  # Koharu uses greedy decoding too.
        return text, len(tokenizer.encode(text, add_special_tokens=False)) >= maximum

    return recognize


def validated_crop(request):
    from app.services.ocr.providers import _crop

    box, padding = request.get("bbox"), request.get("padding", 4)
    if (not isinstance(box, list) or len(box) != 4
            or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in box)
            or min(box[2:]) <= 0 or not isinstance(padding, int) or not 0 <= padding <= 50):
        raise ValueError("invalid OCR crop")
    maximum = request.get("max_new_tokens", LOCAL_OCR_DEFAULT_MAX_TOKENS)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 16 <= maximum <= LOCAL_OCR_MAX_TOKENS:
        raise ValueError("invalid OCR generation limit")
    image = _crop(Path(request["image_path"]), box, padding)
    if not image.width or not image.height:
        raise ValueError("empty OCR crop")
    return image, maximum


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=LOCAL_OCR_MODELS, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--auxiliary", type=Path)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import torch

            torch.set_num_threads(2)
            device = args.device
            if device == "auto":
                device = "cuda:0" if torch.cuda.is_available() else "cpu"
            if device.startswith("cuda") and not torch.cuda.is_available():
                device = "cpu"
            recognize = build_model(args.provider, args.directory, args.auxiliary, device)
        send(ready=True, provider=args.provider, device=device)
    except Exception as exc:
        send(error=f"OCR load failed: {type(exc).__name__}: {exc}"[:1000])
        return
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
                image, maximum = validated_crop(request)
                text, truncated = recognize(image, maximum)
            if not isinstance(text, str) or len(text) > 65536:
                raise ValueError("invalid OCR output")
            send(id=request["id"], text=text.strip(), truncated=bool(truncated))
        except Exception as exc:
            send(id=request.get("id"), error=f"OCR inference failed: {type(exc).__name__}: {exc}"[:1000])


if __name__ == "__main__":
    main()
