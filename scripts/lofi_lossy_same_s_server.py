#!/usr/bin/env python3
"""Tiny local web UI server for scripts/lofi_lossy_same_s.py."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "lofi_lossy_same_s.py"
HTML = ROOT / "scripts" / "lofi_lossy_same_s.html"
RUNS_DIR = Path("/tmp/sa3-lofi-web")

CHOICES = {
    "mode": {"energy", "channels", "sparse", "svd"},
    "op": {"mean", "mix", "add", "subtract", "difference", "multiply", "interleave", "concat"},
    "align": {"shortest", "longest-pad", "first"},
    "op_rms": {"none", "first", "mean"},
    "normalize": {"none", "peak", "tanh"},
}

app = FastAPI()


def clean_name(name: str) -> str:
    stem = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return cleaned or "audio.wav"


def validate_choice(field: str, value: str) -> str:
    if value not in CHOICES[field]:
        raise HTTPException(400, f"invalid {field}: {value}")
    return value


def run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise HTTPException(404, "unknown run")
    return RUNS_DIR / run_id


def output_url(run_id: str, path: str | None) -> str | None:
    if not path:
        return None
    return f"/runs/{run_id}/{Path(path).name}"


async def save_uploads(run_path: Path, uploads: list[UploadFile]) -> list[Path]:
    input_dir = run_path / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for index, upload in enumerate(uploads):
        filename = f"{index + 1:02d}_{clean_name(upload.filename or 'audio.wav')}"
        target = input_dir / filename
        with target.open("wb") as f:
            while chunk := await upload.read(1024 * 1024):
                f.write(chunk)
        if target.stat().st_size == 0:
            raise HTTPException(400, f"empty upload: {upload.filename}")
        saved.append(target)
    return saved


def build_command(
    input_paths: list[Path],
    out_dir: Path,
    levels: str,
    mode: str,
    op: str,
    weights: str,
    align: str,
    gain: float,
    op_rms: str,
    tail_noise: float,
    seed: int,
    device: str,
    chunked: bool,
    chunk_size: int,
    overlap: int,
    normalize: str,
    model_dir: str,
    local_only: bool,
) -> list[str]:
    cmd = [
        sys.executable,
        str(SCRIPT),
        *[str(path) for path in input_paths],
        "--out-dir",
        str(out_dir),
        "--levels",
        levels,
        "--mode",
        mode,
        "--tail-noise",
        str(tail_noise),
        "--seed",
        str(seed),
        "--device",
        device,
        "--chunk-size",
        str(chunk_size),
        "--overlap",
        str(overlap),
        "--normalize",
        normalize,
    ]
    if len(input_paths) > 1:
        cmd.extend(["--op", op, "--align", align, "--gain", str(gain), "--op-rms", op_rms])
        if weights.strip():
            cmd.extend(["--weights", weights.strip()])
    if chunked:
        cmd.append("--chunked")
    if model_dir.strip():
        cmd.extend(["--model-dir", model_dir.strip()])
    if local_only:
        cmd.append("--local-only")
    return cmd


@app.get("/", response_class=HTMLResponse)
async def index():
    if not HTML.exists():
        raise HTTPException(500, "frontend file missing")
    return HTML.read_text()


@app.get("/api/health")
async def health():
    return {"ok": True, "script": str(SCRIPT)}


@app.post("/api/run")
async def run_effect(
    files: list[UploadFile] = File(...),
    levels: str = Form("1.0,0.7,0.4,0.15"),
    mode: str = Form("energy"),
    op: str = Form("mix"),
    weights: str = Form(""),
    align: str = Form("shortest"),
    gain: float = Form(1.0),
    op_rms: str = Form("mean"),
    tail_noise: float = Form(0.0),
    seed: int = Form(0),
    device: str = Form("auto"),
    chunked: bool = Form(False),
    chunk_size: int = Form(64),
    overlap: int = Form(8),
    normalize: str = Form("peak"),
    model_dir: str = Form(""),
    local_only: bool = Form(True),
):
    if not files:
        raise HTTPException(400, "upload at least one audio file")
    if len(files) == 1 and weights.strip():
        raise HTTPException(400, "weights require multiple inputs")
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise HTTPException(400, "overlap must be >= 0 and smaller than chunk size")
    if tail_noise < 0:
        raise HTTPException(400, "tail noise must be >= 0")

    mode = validate_choice("mode", mode)
    op = validate_choice("op", op)
    align = validate_choice("align", align)
    op_rms = validate_choice("op_rms", op_rms)
    normalize = validate_choice("normalize", normalize)

    run_id = f"{int(time.time())}-{uuid4().hex[:8]}"
    job_dir = run_dir(run_id)
    out_dir = job_dir / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    input_paths = await save_uploads(job_dir, files)

    cmd = build_command(
        input_paths=input_paths,
        out_dir=out_dir,
        levels=levels,
        mode=mode,
        op=op,
        weights=weights,
        align=align,
        gain=gain,
        op_rms=op_rms,
        tail_noise=tail_noise,
        seed=seed,
        device=device,
        chunked=chunked,
        chunk_size=chunk_size,
        overlap=overlap,
        normalize=normalize,
        model_dir=model_dir,
        local_only=local_only,
    )

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await process.communicate()
    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")
    if process.returncode != 0:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "run_id": run_id, "stdout": stdout, "stderr": stderr},
        )

    manifests = sorted(out_dir.glob("*_manifest.json"), key=lambda p: p.stat().st_mtime)
    if not manifests:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "run_id": run_id, "stdout": stdout, "stderr": stderr, "error": "manifest missing"},
        )

    manifest_path = manifests[-1]
    manifest = json.loads(manifest_path.read_text())
    reconstruction = manifest.get("reconstruction", {})
    files_out = manifest.get("files", [])
    return {
        "ok": True,
        "run_id": run_id,
        "stdout": stdout,
        "stderr": stderr,
        "manifest_url": output_url(run_id, str(manifest_path)),
        "reconstruction": {
            **reconstruction,
            "url": output_url(run_id, reconstruction.get("path")),
        },
        "files": [
            {
                **item,
                "url": output_url(run_id, item.get("path")),
            }
            for item in files_out
        ],
        "manifest": manifest,
    }


@app.get("/runs/{run_id}/{filename:path}")
async def get_output(run_id: str, filename: str):
    base = (run_dir(run_id) / "outputs").resolve()
    target = (base / filename).resolve()
    if base not in target.parents and target != base:
        raise HTTPException(404, "unknown output")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "unknown output")
    return FileResponse(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the SAME-S lo-fi web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5188)
    args = parser.parse_args()

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
