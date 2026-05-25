# SA3 Studio NVIDIA Reimplementation Spec

Draft status: concrete implementation specification for the NVIDIA rewrite. This document defines the shape of a CUDA-first app deployed over SSH to `m19182-server`, preserving the useful parts of the current app, turning the backend into a reusable library, and leaving room for the latent-space ideas in `TODO.md`.

## 1. Product Direction

Build a local-first AI audio studio for Stable Audio 3 on the NVIDIA server reachable as `ssh m19182-server`.

The app should feel like an editor, not a prompt form:

- Load or generate a single stereo audio track.
- Paint time regions directly on the waveform/spectrogram.
- Regenerate painted regions in place.
- Vary an existing track with controllable strength.
- Stack LoRAs and hear the result quickly.
- Save, reload, search, and reuse audio from a local library.
- Expose the same core operations through a Python library and CLI.

The Apple Silicon/MLX work can remain as historical/reference code, but the new implementation should be CUDA-first and PyTorch-native. The default model for v1 is **Stable Audio 3 Small/SFX**, with Medium supported as an optional model once the CUDA path is stable.

## 2. Target Users

- Local music/sound-design users with an NVIDIA GPU.
- Developers who want SA3 generation/inpainting as a Python library.
- Power users building a personal library of generated clips, source audio, LoRAs, prompts, masks, and variants.

Not the initial target:

- Cloud multi-user SaaS.
- Mobile/touch workflows.
- DAW plugin/VST.
- Multi-track arrangement.

## 3. Target Server

Deployment target:

- Host alias: `m19182-server`.
- SSH command: `ssh -F /home/m19182/.ssh/config m19182-server`.
- Remote user/home: `mateo`, `/home/mateo`.
- OS: Ubuntu 24.04.4 LTS, kernel `6.17.0-23-generic`.
- CPU: AMD Ryzen 9 5950X, 16 cores / 32 threads.
- RAM: 62 GiB, 8 GiB swap.
- GPU: NVIDIA GA102 GeForce RTX 3090, expected 24 GB VRAM.
- Disk: root/home on 916 GB NVMe, 164 GB free at inspection time.
- Node: `v22.22.0`; npm: `10.9.4`.
- Python: system `python3` is `3.12.3`.
- Docker: `29.2.1` is installed.
- `uv`: installed at `/home/mateo/.local/bin/uv`, version `0.11.3`, but not on the non-interactive SSH `PATH`.
- Hugging Face CLI: installed at `/home/mateo/.local/bin/hf`, but not on the non-interactive SSH `PATH`.
- Global Python does not currently have `torch`.
- Existing audio AI repos live under `/home/mateo/projects/ai/sound`.
- No SA3 model directory was found under `/home/mateo/projects/ai/sound` or Hugging Face cache during inspection.
- No `/home/mateo/loras` or `/home/mateo/.sa3-studio` directory exists yet.

Current deployment blocker:

- `nvidia-smi` fails with `Failed to initialize NVML: Driver/library version mismatch`.
- Loaded kernel module: `/sys/module/nvidia/version` reports `580.126.09`.
- Installed userspace/packages: `nvidia-driver-580`, `nvidia-dkms-580`, `nvidia-utils-580`, and `libnvidia-compute-580` are `580.159.03`.
- `modinfo nvidia` points to a `580.159.03` DKMS module, so the likely fix is a reboot or unloading/reloading the NVIDIA modules so the running kernel uses `580.159.03`.
- CUDA work should not start until `nvidia-smi` succeeds.

Remote paths:

- Project checkout: `/home/mateo/projects/ai/sound/sa3-studio`.
- App state/library: `/home/mateo/.sa3-studio`.
- Model base dir: `/home/mateo/.sa3-studio/models`.
- Default model dir: `/home/mateo/.sa3-studio/models/stable-audio-3-small-sfx-base`.
- Optional medium model dir: `/home/mateo/.sa3-studio/models/stable-audio-3-medium`.
- LoRA dir: `/home/mateo/.sa3-studio/loras`.

Runtime target:

- Python 3.12 unless a dependency forces 3.11.
- PyTorch CUDA wheels.
- `stable-audio-3` or a vendored compatible runtime.
- Flash Attention only when required/beneficial by the selected SA3 model.

Expected GPU tiers:

- RTX 3090 / 24 GB VRAM: primary baseline, Small/SFX default, Medium optional.
- 16 GB+ VRAM: likely workable for Medium with careful caching.
- 12 GB VRAM: Small/SFX target, Medium may require shorter clips/offload.
- 8 GB VRAM: best-effort Small/SFX only.

Runtime policy:

- Prefer `cuda` automatically when available.
- Use fp16/bf16 by default for model inference.
- Keep CPU fallback only for non-generation tasks and tests.
- Make device placement explicit per subsystem: DiT, autoencoder, text encoder, library embedding jobs.
- Fail fast if `nvidia-smi` or `torch.cuda.is_available()` is false on `m19182-server`.

## 4. Current Functionality To Preserve

From the existing app:

- Text-to-audio generation.
- Source upload and resampling to 44.1 kHz stereo.
- Audio-to-audio vary mode via source audio plus noise strength.
- Time-only inpainting with a binary latent mask.
- In-place stitching so unmasked regions preserve original samples.
- Spectrogram visualization.
- Overview waveform.
- Per-latent envelope/waveform data.
- Paint-on-canvas mask editing.
- Scroll/pinch zoom anchored at cursor.
- Shift-scroll or drag-based panning.
- Click-to-scrub playhead.
- Playback lowpass/ducking over masked regions.
- Ghost overlay for last inpainted regions.
- LoRA stack with strength sliders.
- Model switching between local model directories.
- Local library save/load/delete for source audio and generations.
- Live system stats: CPU, RAM, GPU/VRAM, current model, backend status.

Known gaps to design into the rewrite:

- Variant history and undo/redo.
- Explicit sessions.
- Per-region prompts.
- Streaming progress/previews.
- Searchable library.
- Better job lifecycle: queue, cancel, progress, logs.
- Packaging and install clarity for NVIDIA users.

## 5. New Project Shape

Proposed monorepo layout:

```text
sa3-studio/
  pyproject.toml
  README.md
  SPEC.md
  sa3_studio/
    core/
      runtime.py          # model loading, device policy, precision policy
      generation.py       # text-to-audio, vary, inpaint
      masks.py            # latent/audio mask conversion and stitching
      audio.py            # load, resample, normalize, write
      viz.py              # envelope, spectrogram, overview renderers
      lora.py             # LoRA discovery, stack config, load/apply
      models.py           # model registry and local model validation
    library/
      store.py            # filesystem layout and artifact writes
      db.py               # SQLite schema and queries
      index.py            # vector index abstraction
      embeddings.py       # latent/audio/text embedding jobs
      search.py           # reference, text, humming, mood-board search
    jobs/
      queue.py            # one active GPU job, cancellable queued jobs
      events.py           # progress events and websocket payloads
    api/
      app.py              # FastAPI assembly
      routes.py           # REST endpoints
      websocket.py        # progress/preview stream
      schemas.py          # Pydantic request/response models
    cli.py
  webui/
    src/
      lib/
      routes or app shell
  tests/
```

Library/API split:

- `sa3_studio.core` must be usable without the web server.
- `sa3_studio.library` must be usable from scripts for batch indexing/search.
- `sa3_studio.api` should be a thin service layer around the library.
- `webui` should never encode model rules directly; it sends masks/settings and renders returned state.

Remote development loop:

1. Develop locally in this repo or directly over SSH.
2. Push or sync to `/home/mateo/projects/ai/sound/sa3-studio`.
3. Run backend on the server bound to `127.0.0.1` by default.
4. Access via SSH port forward, for example local `5173` -> remote frontend and local `5174` -> remote API.
5. Add LAN binding only after local-only auth/security choices are made.

## 6. Core Python Library API

Initial Python API sketch:

```python
from sa3_studio import SA3Studio

studio = SA3Studio.from_local_model(
    model_dir="/home/mateo/.sa3-studio/models/stable-audio-3-small-sfx-base",
    device="cuda",
    dtype="float16",
)

audio = studio.generate(
    prompt="tight industrial drum loop",
    duration=12.0,
    steps=8,
    cfg_scale=1.0,
    seed=1234,
)

edited = studio.inpaint(
    source="input.wav",
    mask=[0, 0, 1, 1, 1, 0],
    prompt="clean snare fill",
    steps=8,
    cfg_scale=1.0,
    seed=1235,
)

variant = studio.vary(
    source="input.wav",
    prompt="same groove, warmer tape texture",
    noise_level=0.35,
    seed=99,
)
```

Core result object:

- Audio samples, sample rate, channel count.
- Optional latents.
- Prompt/settings/seed.
- Timing metrics: conditioning, DiT, AE, render, total.
- Model id and LoRA stack.
- Warnings: truncation, clipping risk, fallback path, missing CUDA feature.

## 7. Backend API

Keep FastAPI, but move logic out of route handlers.

REST endpoints:

- `GET /api/state`
- `GET /api/stats`
- `GET /api/models`
- `POST /api/models/switch`
- `GET /api/loras`
- `POST /api/upload`
- `POST /api/generate`
- `POST /api/jobs/{id}/cancel`
- `GET /api/audio/{asset_id}`
- `GET /api/viz/{asset_id}/spectrogram.png`
- `GET /api/viz/{asset_id}/overview.png`
- `GET /api/library`
- `POST /api/library/import`
- `POST /api/library/save-current`
- `POST /api/library/load`
- `POST /api/library/delete`
- `POST /api/library/search`
- `POST /api/sessions`
- `GET /api/sessions/{id}`
- `PUT /api/sessions/{id}`

WebSocket:

- `WS /api/events`
- Emits model loading, job queued/running/progress/completed/failed, stats ticks, preview frames, library indexing progress.

Job behavior:

- Only one active GPU generation job by default.
- Queue optional but visible.
- User can cancel queued jobs and best-effort cancel active jobs.
- Every job writes a durable record with settings, logs, timings, and output artifacts.

## 8. Frontend

Keep **Svelte**. The existing Svelte app is already close to the desired interaction model and is easier to evolve than starting over.

Primary screens:

- Editor: current single-track workflow.
- Library: searchable local asset browser.
- Job drawer: active/recent jobs, failures, timings.
- Settings: model paths, library path, LoRA path, CUDA/runtime settings.

Editor requirements:

- One track visible at a time.
- Main visualization toggles spectrogram/waveform.
- Latent mask is the source of truth.
- Regions are derived from contiguous mask runs.
- Generate button changes behavior by state:
  - No source: generate.
  - Source + mask: inpaint.
  - Source + no mask + noise > 0: vary.
  - Source + no mask + noise = 0: hidden/disabled with hint.
- Variant history is part of the session, not just saved WAV files.
- Undo/redo covers mask edits, prompt/settings changes, source changes, and variant selection.
- Progress events should update the UI without blocking stats/playback.

Library UI requirements:

- Browse sources, generations, sessions, LoRAs, and indexed clips.
- Filter by kind, date, duration, model, prompt text, tags, sample rate.
- Search by text first; reference-audio and humming search can come later.
- Load an item into the editor.
- Save current editor output as a library item.
- Show provenance: prompt, seed, model, LoRAs, source, mask, parent variant.

## 9. Local Library

The local library becomes a real subsystem, not just folders of WAV files.

Filesystem layout:

```text
/home/mateo/.sa3-studio/
  library.db
  assets/
    audio/
    generations/
    previews/
    sessions/
    embeddings/
  models.json
  settings.json
```

SQLite tables:

- `assets`: id, kind, path, name, duration, sample_rate, channels, created_at, updated_at.
- `generations`: asset_id, prompt, negative_prompt, seed, steps, cfg, noise, model_id, parent_asset_id.
- `lora_uses`: generation_id, lora_path, strength.
- `sessions`: id, name, current_asset_id, state_json, created_at, updated_at.
- `variants`: session_id, asset_id, index, state_json.
- `tags`: id, name.
- `asset_tags`: asset_id, tag_id.
- `embeddings`: asset_id, embedding_type, path, dims, model, created_at.

Search/index phases:

1. Metadata and prompt text search with SQLite FTS.
2. SA3 latent-derived similarity search for loaded/generated clips.
3. Reference-audio search.
4. Humming search.
5. Mood boards: a named collection whose centroid or weighted query retrieves similar clips.

Vector index candidates:

- Start simple with NumPy arrays plus brute force for small libraries.
- Add FAISS or another ANN backend when the library grows.
- Keep the index abstraction independent so the backend can change.

## 10. TODO Ideas Mapped To Product Phases

Phase 1: CUDA parity app

- Audio inpainting tool.
- Text-to-audio.
- Audio-to-audio variation.
- LoRA stack.
- Local library basics.

Phase 2: Editor quality

- Variant history.
- Undo/redo.
- Session save/load.
- Job queue/cancel/progress.
- Streaming generation progress.
- Better spectrogram/latent previews.

Phase 3: Searchable creative library

- Semantic audio search/retrieval.
- Search by prompt, reference audio, and eventually humming.
- Mood-board collections.
- Batch indexing.

Phase 4: Latent tools

- Latent space audio explorer.
- Interpolate between encoded tracks.
- Lo-fi/lossy compression art controls.
- Latent-space mixing/DJ transitions.
- Style transfer experiments.

Phase 5: Live/performance experiments

- Generative loop machine.
- Real-time encode/transform/decode.
- Low-latency constraints and possibly a separate audio engine.

Research/parking lot:

- Audio watermarking/steganography.
- Frequency-bounded inpainting, unless model-side support exists.
- Multi-track/arrangement.
- DAW plugin.

## 11. NVIDIA-Specific Engineering Plan

CUDA model runtime:

- Remove ROCm-specific dependency indexes from the NVIDIA project.
- Use official PyTorch CUDA wheels.
- Detect CUDA capability and available VRAM at startup.
- Fail early with actionable messages if required kernels/packages are missing.
- Keep all model-specific setup in `sa3_studio.core.runtime`.
- First `doctor` check on `m19182-server` must verify the NVIDIA driver mismatch is fixed.
- Default runtime model is `stable-audio-3-small-sfx-base`; Medium is an explicit model switch.

Memory policy:

- Default to half precision on CUDA.
- Make AE device configurable: `cuda` for speed, `cpu` for VRAM relief.
- Clear CUDA cache when switching models.
- Track peak memory per generation.
- Add optional CPU offload only if needed for 8 GB GPUs.

Performance policy:

- Cache text conditioning by prompt/duration/model.
- Cache source latents by source hash/model/AE settings.
- Cache visualization artifacts by asset hash.
- Optional preview cache for sliders after the core app is stable.

Packaging:

- Use `/home/mateo/.local/bin/uv` on `m19182-server`, or add `/home/mateo/.local/bin` to PATH for service shells.
- Explicit CUDA install instructions.
- Dockerfile optional after local install is proven.
- A `sa3-studio doctor` command should validate CUDA, model paths, weights, LoRAs, and library permissions.

Server bootstrap checklist:

1. Fix NVIDIA driver state until `nvidia-smi` works.
2. Create `/home/mateo/projects/ai/sound/sa3-studio`.
3. Create `/home/mateo/.sa3-studio/models`, `/home/mateo/.sa3-studio/loras`, and `/home/mateo/.sa3-studio/assets`.
4. Ensure `/home/mateo/.local/bin` is on PATH for non-interactive SSH or use absolute paths in scripts.
5. Install/sync Python dependencies into a project-local virtualenv with `uv`.
6. Install frontend dependencies with npm.
7. Download the gated default Small/SFX SA3 model into `/home/mateo/.sa3-studio/models/stable-audio-3-small-sfx-base`.
8. Run `sa3-studio doctor`.
9. Start backend and frontend with SSH port forwarding.

## 12. Tests And Verification

Unit tests:

- Mask conversion: latent mask to audio mask, inversion, padding/truncation.
- Stitching/crossfade boundaries.
- Library id/path validation.
- SQLite schema migrations.
- Job state transitions.
- Settings serialization.

Integration tests:

- Load model metadata without full generation.
- Generate a tiny/short sample when weights are available.
- Upload/resample/load audio.
- Save/load/delete library assets.
- Session round trip.

Manual verification:

- CUDA device selected.
- `nvidia-smi` succeeds and reports RTX 3090.
- Small/SFX model loads as the default.
- Optional: Medium model loads after the Small/SFX path is stable.
- Generate, vary, and inpaint each complete.
- Unmasked inpaint regions preserve source samples.
- LoRA stack changes output and records provenance.
- Frontend can survive backend restart and show clear errors.

## 13. Migration From Current Repo

Keep or port:

- Svelte UI components and visual interaction patterns.
- FastAPI route concepts.
- Spectrogram/overview/envelope renderers.
- Mask semantics and in-place stitch logic.
- LoRA list/stack UX.
- Local model directory convention.

Replace or isolate:

- MLX-specific autoencoder code.
- ROCm-specific environment defaults and dependency indexes.
- Global backend state in `backend/server.py`.
- Hard-coded temp paths and model paths.
- Route handlers that directly own model/generation logic.

Decision:

- The rewrite should probably be a clean package inside this repo first, then the old backend can be retired once parity is reached.
- Avoid a flag-heavy single `server.py`; split the library now so the Python API, CLI, and frontend all use the same implementation.

## 14. Open Questions For Mateo

Answered:

- Target environment is SSH to `m19182-server`.
- Baseline GPU is the server RTX 3090 class card.
- Default model is Small/SFX.
- Frontend stays Svelte.

Remaining:

1. Should the app stay single-user/local-only behind SSH port forwarding, or should the backend be designed for LAN access?
2. Is a Python library API a hard requirement for v1, or can it emerge after backend parity?
3. Should the local library store only WAVs, or should it also support FLAC/MP3 imports while writing WAV outputs?
4. Do you want prompt/tag/search metadata to be manually editable in the UI?
5. Which search matters first: prompt text, reference audio, humming, or latent similarity?
6. Do you want generated variants saved automatically to the library, or only when explicitly saved?
7. Should per-region prompts be v1, or should v1 keep one global prompt?
8. Should active generation cancellation be required, or is canceling queued jobs enough for v1?
9. Should LoRA loading be hot-swappable during a running session, or only before generation starts?
10. Is Docker a goal, or should the install path stay native `uv` first?
11. Do you want this to remain named `sa3-inpainter-ui`, or should the rewrite become `sa3-studio`?

## 15. Suggested V1 Acceptance Criteria

V1 is done when:

- Fresh install on `m19182-server` succeeds from documented steps.
- NVIDIA driver health is green: `nvidia-smi` works and `torch.cuda.is_available()` is true.
- `sa3-studio doctor` confirms CUDA, model weights, library dir, and LoRA dir.
- Frontend opens and shows model/device/library status.
- User can generate from text using Small/SFX by default.
- User can upload audio, paint a time mask, and inpaint it.
- User can vary uploaded audio with a noise slider.
- User can add/remove LoRAs with strength sliders.
- User can browse the local library, load a saved item, and delete an item.
- User can save/reopen an editor session with variants.
- Python API can call generate, vary, and inpaint without starting the web app.
- Tests cover mask/stitch/library/job/session logic.
