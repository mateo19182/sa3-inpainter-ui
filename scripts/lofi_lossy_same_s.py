#!/usr/bin/env python3
"""Latent-space lo-fi / lossy compression art with the SAME-S autoencoder.

Encodes an input audio file with the Stable Audio 3 Small autoencoder, truncates
the latent representation at several levels, then decodes each version back to
audio. The default truncation is energy-aware: latent channels with the largest
RMS energy survive first, which tends to preserve broad musical structure while
discarding detail in a more semantic way than waveform-domain bitcrushing.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stable_audio_3.loading_utils import load_autoencoder  # noqa: E402
from stable_audio_3.model_configs import ae_models  # noqa: E402


SMALL_MODEL_NAME = "stable-audio-3-small-sfx-base"
DEFAULT_LEVELS = "1.0,0.85,0.7,0.55,0.4,0.28,0.18,0.1,0.05"


@dataclass(frozen=True)
class ModelPaths:
    config: Path
    ckpt: Path
    source: str


@dataclass(frozen=True)
class EncodedInput:
    path: Path
    input_sample_rate: int
    source_samples: int
    target_samples: int
    prepared_shape: tuple[int, ...]
    latents: torch.Tensor


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if platform.system() == "Darwin" and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def existing_model_dir_paths(model_dir: Path) -> ModelPaths | None:
    config = model_dir / "model_config.json"
    ckpt = model_dir / "model.safetensors"
    if config.exists() and ckpt.exists():
        return ModelPaths(config=config, ckpt=ckpt, source=str(model_dir))
    return None


def discover_same_s_paths(args: argparse.Namespace) -> ModelPaths:
    if args.config or args.ckpt:
        if not (args.config and args.ckpt):
            raise SystemExit("--config and --ckpt must be provided together")
        config = Path(args.config).expanduser()
        ckpt = Path(args.ckpt).expanduser()
        if not config.exists():
            raise SystemExit(f"config not found: {config}")
        if not ckpt.exists():
            raise SystemExit(f"checkpoint not found: {ckpt}")
        return ModelPaths(config=config, ckpt=ckpt, source="explicit files")

    candidates: list[Path] = []
    if args.model_dir:
        candidates.append(Path(args.model_dir).expanduser())
    if os.environ.get("SA3_MODEL_DIR"):
        candidates.append(Path(os.environ["SA3_MODEL_DIR"]).expanduser())
    if os.environ.get("SAME_S_MODEL_DIR"):
        candidates.append(Path(os.environ["SAME_S_MODEL_DIR"]).expanduser())

    models_base = Path(os.environ.get("SA3_MODELS_DIR", Path.home() / ".sa3-studio/models"))
    candidates.append(models_base / SMALL_MODEL_NAME)
    candidates.append(Path.home() / "Projects/stable-audio-3/models" / SMALL_MODEL_NAME)

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        paths = existing_model_dir_paths(candidate)
        if paths is not None:
            return paths

    if args.local_only:
        searched = "\n  ".join(str(p) for p in seen)
        raise SystemExit(f"SAME-S model files not found. Searched:\n  {searched}")

    config, ckpt = ae_models["same-s"].resolve()
    return ModelPaths(config=Path(config), ckpt=Path(ckpt), source="HuggingFace same-s resolver")


def parse_levels(raw: str) -> list[float]:
    levels = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        value = float(part)
        if not 0.0 < value <= 1.0:
            raise argparse.ArgumentTypeError("levels must be in the range (0, 1]")
        levels.append(value)
    if not levels:
        raise argparse.ArgumentTypeError("at least one level is required")
    return levels


def slugify(value: Path | str) -> str:
    stem = value.stem if isinstance(value, Path) else value
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "audio"


def parse_weights(raw: str | None, count: int, device: torch.device) -> torch.Tensor:
    if raw is None:
        weights = [1.0] * count
    else:
        weights = [float(part.strip()) for part in raw.split(",") if part.strip()]
        if len(weights) != count:
            raise SystemExit(f"--weights expects {count} values for {count} inputs")
    return torch.tensor(weights, device=device, dtype=torch.float32)


def read_audio(path: Path) -> tuple[torch.Tensor, int]:
    audio, sr = sf.read(path, always_2d=True, dtype="float32")
    # soundfile returns [samples, channels]; autoencoder expects [channels, samples].
    return torch.from_numpy(audio.T.copy()), int(sr)


def write_audio(path: Path, audio: torch.Tensor, sample_rate: int, normalize: str) -> float:
    audio = audio.detach().to(torch.float32).cpu()
    peak = float(audio.abs().max().item()) if audio.numel() else 0.0
    if normalize == "peak" and peak > 1.0:
        audio = audio / peak
    elif normalize == "tanh":
        audio = torch.tanh(audio)
    audio = audio.clamp(-1.0, 1.0)
    subtype = "FLOAT" if path.suffix.lower() == ".wav" else None
    sf.write(path, audio.T.numpy(), sample_rate, subtype=subtype)
    return peak


def encode_input(path: Path, ae, device: str, args: argparse.Namespace) -> EncodedInput:
    source_audio, input_sr = read_audio(path)
    original_seconds = source_audio.shape[-1] / input_sr
    prepared = ae.preprocess_audio_for_encoder(source_audio, input_sr).to(device)
    target_samples = int(round(original_seconds * ae.sample_rate))

    with torch.inference_mode():
        latents = ae.encode_audio(
            prepared,
            chunked=args.chunked,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
        )

    return EncodedInput(
        path=path,
        input_sample_rate=input_sr,
        source_samples=source_audio.shape[-1],
        target_samples=target_samples,
        prepared_shape=tuple(prepared.shape),
        latents=latents,
    )


def resize_latents(latents: torch.Tensor, target_latents: int) -> torch.Tensor:
    current = latents.shape[-1]
    if current == target_latents:
        return latents
    if current > target_latents:
        return latents[..., :target_latents]
    return torch.nn.functional.pad(latents, (0, target_latents - current))


def align_latents(encoded: list[EncodedInput], strategy: str) -> tuple[list[torch.Tensor], int]:
    lengths = [item.latents.shape[-1] for item in encoded]
    if strategy == "shortest":
        target_latents = min(lengths)
    elif strategy == "longest-pad":
        target_latents = max(lengths)
    elif strategy == "first":
        target_latents = lengths[0]
    else:
        raise ValueError(f"unknown alignment: {strategy}")
    return [resize_latents(item.latents, target_latents) for item in encoded], target_latents


def match_rms(reference: torch.Tensor, value: torch.Tensor) -> torch.Tensor:
    ref_rms = reference.float().pow(2).mean().sqrt()
    value_rms = value.float().pow(2).mean().sqrt()
    return value * (ref_rms / value_rms.clamp_min(1e-8)).to(value.dtype)


def combine_latents(
    encoded: list[EncodedInput],
    op: str,
    align: str,
    weights_raw: str | None,
    gain: float,
    op_rms: str,
) -> tuple[torch.Tensor, int, dict[str, object]]:
    if len(encoded) == 1:
        return encoded[0].latents, encoded[0].target_samples, {
            "operation": "single",
            "alignment": None,
            "gain": 1.0,
        }

    samples_per_latent = encoded[0].prepared_shape[-1] / encoded[0].latents.shape[-1]

    if op == "concat":
        combined = torch.cat([item.latents for item in encoded], dim=-1)
        target_samples = int(round(combined.shape[-1] * samples_per_latent))
        return combined, target_samples, {
            "operation": op,
            "alignment": None,
            "gain": gain,
            "samples_per_latent": samples_per_latent,
            "segments": [item.latents.shape[-1] for item in encoded],
        }

    aligned, target_latents = align_latents(encoded, align)
    stacked = torch.stack(aligned, dim=0)
    weights = parse_weights(weights_raw, len(encoded), aligned[0].device)
    view_weights = weights.view(-1, 1, 1, 1).to(stacked.dtype)

    if op in ("mean", "mix"):
        if op == "mean" and weights_raw is None:
            combined = stacked.mean(dim=0)
        else:
            denom = weights.abs().sum().clamp_min(1e-8).to(stacked.dtype)
            combined = (stacked * view_weights).sum(dim=0) / denom
    elif op == "add":
        combined = (stacked * view_weights).sum(dim=0)
    elif op == "subtract":
        combined = aligned[0] * weights[0].to(aligned[0].dtype)
        for item, weight in zip(aligned[1:], weights[1:]):
            combined = combined - item * weight.to(item.dtype)
    elif op == "difference":
        denom = weights[1:].abs().sum().clamp_min(1e-8).to(stacked.dtype)
        rest = (stacked[1:] * view_weights[1:]).sum(dim=0) / denom
        combined = aligned[0] * weights[0].to(aligned[0].dtype) - rest
    elif op == "multiply":
        rms_stack = stacked.float().pow(2).mean(dim=(1, 2, 3), keepdim=True).sqrt().clamp_min(1e-8)
        normalized = stacked / rms_stack.to(stacked.dtype)
        combined = normalized.prod(dim=0)
    elif op == "interleave":
        combined = torch.empty_like(aligned[0])
        for frame in range(target_latents):
            combined[..., frame] = aligned[frame % len(aligned)][..., frame]
    else:
        raise ValueError(f"unknown operation: {op}")

    combined = combined * gain
    if op_rms == "first":
        combined = match_rms(aligned[0], combined)
    elif op_rms == "mean":
        combined = match_rms(stacked.mean(dim=0), combined)
    elif op_rms != "none":
        raise ValueError(f"unknown RMS mode: {op_rms}")

    if align == "shortest":
        target_samples = min(item.target_samples for item in encoded)
    elif align == "longest-pad":
        target_samples = max(item.target_samples for item in encoded)
    else:
        target_samples = encoded[0].target_samples

    return combined, target_samples, {
        "operation": op,
        "alignment": align,
        "gain": gain,
        "op_rms": op_rms,
        "weights": [float(v) for v in weights.detach().cpu().tolist()],
        "aligned_latents": target_latents,
        "samples_per_latent": samples_per_latent,
    }


def channel_energy(latents: torch.Tensor) -> torch.Tensor:
    # Average over batch and time, returning one score per latent channel.
    return latents.float().pow(2).mean(dim=(0, 2))


def energy_mask(latents: torch.Tensor, level: float) -> tuple[torch.Tensor, dict[str, float | int]]:
    scores = channel_energy(latents)
    order = torch.argsort(scores, descending=True)
    if level >= 1.0:
        keep = len(order)
    else:
        cumulative = torch.cumsum(scores[order], dim=0)
        total = cumulative[-1].clamp_min(1e-12)
        keep = int(torch.searchsorted(cumulative / total, torch.tensor(level, device=scores.device)).item()) + 1
    mask = torch.zeros_like(scores, dtype=latents.dtype)
    mask[order[:keep]] = 1
    kept_energy = float(scores[order[:keep]].sum().div(scores.sum().clamp_min(1e-12)).item())
    return mask.view(1, -1, 1), {"kept_channels": keep, "kept_energy": kept_energy}


def channel_fraction_mask(latents: torch.Tensor, level: float) -> tuple[torch.Tensor, dict[str, float | int]]:
    scores = channel_energy(latents)
    order = torch.argsort(scores, descending=True)
    keep = max(1, int(round(scores.numel() * level)))
    mask = torch.zeros_like(scores, dtype=latents.dtype)
    mask[order[:keep]] = 1
    kept_energy = float(scores[order[:keep]].sum().div(scores.sum().clamp_min(1e-12)).item())
    return mask.view(1, -1, 1), {"kept_channels": keep, "kept_energy": kept_energy}


def frame_sparse(latents: torch.Tensor, level: float) -> tuple[torch.Tensor, dict[str, float | int]]:
    channels = latents.shape[1]
    keep = max(1, int(round(channels * level)))
    _, idx = torch.topk(latents.abs(), k=keep, dim=1)
    sparse = torch.zeros_like(latents)
    sparse.scatter_(1, idx, latents.gather(1, idx))
    return sparse, {"kept_channels_per_frame": keep}


def svd_truncate(latents: torch.Tensor, level: float) -> tuple[torch.Tensor, dict[str, float | int]]:
    if latents.shape[0] != 1:
        raise ValueError("svd mode expects batch size 1")
    matrix = latents[0].float()
    max_rank = min(matrix.shape)
    rank = max(1, int(round(max_rank * level)))
    u, s, vh = torch.linalg.svd(matrix, full_matrices=False)
    approx = (u[:, :rank] * s[:rank]) @ vh[:rank]
    energy = float(s[:rank].pow(2).sum().div(s.pow(2).sum().clamp_min(1e-12)).item())
    return approx.to(latents.dtype).unsqueeze(0), {"rank": rank, "kept_svd_energy": energy}


def truncate_latents(
    latents: torch.Tensor,
    level: float,
    mode: str,
    noise: float,
    generator: torch.Generator | None,
) -> tuple[torch.Tensor, dict[str, float | int | str]]:
    if mode == "energy":
        mask, info = energy_mask(latents, level)
        degraded = latents * mask
    elif mode == "channels":
        mask, info = channel_fraction_mask(latents, level)
        degraded = latents * mask
    elif mode == "sparse":
        degraded, info = frame_sparse(latents, level)
        mask = (degraded != 0).to(latents.dtype)
    elif mode == "svd":
        degraded, info = svd_truncate(latents, level)
        mask = torch.ones_like(latents)
    else:
        raise ValueError(f"unknown mode: {mode}")

    if noise > 0 and mode != "svd":
        dropped = 1 - mask
        sigma = latents.float().std().to(latents.dtype) * noise
        try:
            filler = torch.randn_like(latents, generator=generator) * sigma
        except TypeError:
            filler = torch.randn_like(latents) * sigma
        degraded = degraded + filler * dropped
        info["tail_noise"] = noise

    info["mode"] = mode
    info["level"] = level
    return degraded, info


def load_same_s_ae(paths: ModelPaths, device: str):
    # safetensors does not reliably load directly to every accelerator backend.
    # Load on CPU first, then move the module.
    ae = load_autoencoder(str(paths.config), str(paths.ckpt), device="cpu")
    ae.eval().requires_grad_(False)
    try:
        ae.bottleneck.noise_regularize = False
    except AttributeError:
        pass
    return ae.to(device)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Encode/decode lossy latent-art variations with SAME-S.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("inputs", type=Path, nargs="+", help="Input WAV/FLAC/AIFF/OGG files readable by libsndfile.")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/lofi-lossy"), help="Directory for rendered files.")
    parser.add_argument("--levels", type=parse_levels, default=parse_levels(DEFAULT_LEVELS), help="Comma-separated truncation levels.")
    parser.add_argument("--mode", choices=("energy", "channels", "sparse", "svd"), default="energy", help="Latent truncation strategy.")
    parser.add_argument(
        "--op",
        choices=("mean", "mix", "add", "subtract", "difference", "multiply", "interleave", "concat"),
        default="mix",
        help="Latent operation used when multiple inputs are provided.",
    )
    parser.add_argument("--weights", help="Comma-separated input weights for mix/add/subtract/difference.")
    parser.add_argument("--align", choices=("shortest", "longest-pad", "first"), default="shortest", help="How to align multi-input latent lengths.")
    parser.add_argument("--gain", type=float, default=1.0, help="Post-operation latent gain before degradation.")
    parser.add_argument("--op-rms", choices=("none", "first", "mean"), default="mean", help="RMS matching after multi-input operations.")
    parser.add_argument("--tail-noise", type=float, default=0.0, help="Add noise into discarded latent components.")
    parser.add_argument("--seed", type=int, default=0, help="Noise seed.")
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, mps, etc.")
    parser.add_argument("--chunked", action="store_true", help="Chunk encode/decode to reduce peak memory.")
    parser.add_argument("--chunk-size", type=int, default=64, help="Chunk size measured in latents.")
    parser.add_argument("--overlap", type=int, default=8, help="Chunk overlap measured in latents.")
    parser.add_argument("--normalize", choices=("none", "peak", "tanh"), default="peak", help="Output gain safety.")
    parser.add_argument("--model-dir", type=Path, help="Directory containing SAME-S or SA3-small model_config.json/model.safetensors.")
    parser.add_argument("--config", type=Path, help="Explicit model_config.json for SAME-S or SA3-small.")
    parser.add_argument("--ckpt", type=Path, help="Explicit model.safetensors for SAME-S or SA3-small.")
    parser.add_argument("--local-only", action="store_true", help="Do not download through HuggingFace if local files are missing.")
    args = parser.parse_args()

    input_paths = [path.expanduser() for path in args.inputs]
    missing = [str(path) for path in input_paths if not path.exists()]
    if missing:
        raise SystemExit("input not found:\n  " + "\n  ".join(missing))
    if len(input_paths) == 1 and args.weights:
        raise SystemExit("--weights is only valid with multiple inputs")
    if len(input_paths) == 1 and args.op != "mix":
        raise SystemExit("--op is only valid with multiple inputs")
    if len(input_paths) < 2 and args.align != "shortest":
        raise SystemExit("--align is only valid with multiple inputs")
    if len(input_paths) < 2 and args.op_rms != "mean":
        raise SystemExit("--op-rms is only valid with multiple inputs")
    if args.overlap >= args.chunk_size:
        raise SystemExit("--overlap must be smaller than --chunk-size")
    if args.tail_noise < 0:
        raise SystemExit("--tail-noise must be >= 0")

    device = pick_device(args.device)
    paths = discover_same_s_paths(args)
    out_dir = args.out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    base = slugify(input_paths[0]) if len(input_paths) == 1 else slugify(f"{args.op}_" + "_".join(path.stem for path in input_paths[:4]))

    print(f"[same-s-lofi] model: {paths.source}")
    print(f"[same-s-lofi] device: {device}")
    ae = load_same_s_ae(paths, device)

    encoded = []
    for input_path in input_paths:
        item = encode_input(input_path, ae, device, args)
        encoded.append(item)
        print(f"[same-s-lofi] encoded {input_path}: audio {item.prepared_shape} -> latents {tuple(item.latents.shape)}")

    latents, target_samples, op_info = combine_latents(
        encoded,
        op=args.op,
        align=args.align,
        weights_raw=args.weights,
        gain=args.gain,
        op_rms=args.op_rms,
    )
    print(f"[same-s-lofi] operation: {op_info}")

    manifest: dict[str, object] = {
        "inputs": [
            {
                "path": str(item.path),
                "input_sample_rate": item.input_sample_rate,
                "source_samples": item.source_samples,
                "target_samples": item.target_samples,
                "latent_shape": list(item.latents.shape),
            }
            for item in encoded
        ],
        "output_sample_rate": ae.sample_rate,
        "model_source": paths.source,
        "operation": op_info,
        "mode": args.mode,
        "levels": args.levels,
        "latent_shape": list(latents.shape),
        "files": [],
    }

    generator = None
    if args.tail_noise > 0:
        generator = torch.Generator(device=latents.device)
        generator.manual_seed(args.seed)

    with torch.inference_mode():
        recon = ae.decode_audio(
            latents,
            chunked=args.chunked,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
        )[..., :target_samples]
    recon_path = out_dir / f"{base}_same-s_reconstruction.wav"
    recon_peak = write_audio(recon_path, recon[0], ae.sample_rate, args.normalize)
    manifest["reconstruction"] = {"path": str(recon_path), "pre_normalize_peak": recon_peak}

    for level in args.levels:
        degraded_latents, info = truncate_latents(latents, level, args.mode, args.tail_noise, generator)
        with torch.inference_mode():
            rendered = ae.decode_audio(
                degraded_latents,
                chunked=args.chunked,
                chunk_size=args.chunk_size,
                overlap=args.overlap,
            )[..., :target_samples]

        tag = f"{level:.3f}".replace(".", "p")
        out_path = out_dir / f"{base}_same-s_{args.mode}_{tag}.wav"
        peak = write_audio(out_path, rendered[0], ae.sample_rate, args.normalize)
        info["path"] = str(out_path)
        info["pre_normalize_peak"] = peak
        manifest["files"].append(info)
        print(f"[same-s-lofi] wrote {out_path}  {info}")

    manifest_path = out_dir / f"{base}_same-s_{args.mode}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[same-s-lofi] manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
