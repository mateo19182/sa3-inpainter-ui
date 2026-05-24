// Shared reactive session state.

function maskToRanges(mask) {
  const out = [];
  let start = -1;
  for (let i = 0; i < mask.length; i++) {
    if (mask[i] && start < 0) start = i;
    else if (!mask[i] && start >= 0) { out.push([start, i]); start = -1; }
  }
  if (start >= 0) out.push([start, mask.length]);
  return out;
}

class Session {
  // track
  trackSeconds    = $state(0);
  sampleRate      = $state(44100);
  downsampleRatio = $state(4096);

  // backend session
  version  = $state(0);     // bumped by backend on every change; bust caches
  hasAudio = $state(false);

  // mask is the single source of truth for what's painted
  mask = $state(new Uint8Array(0));
  ghostMask = $state(new Uint8Array(0));   // last-inpainted regions, for visual recall

  // zoom window over full track, normalized 0..1
  zoomStart = $state(0.0);
  zoomEnd   = $state(1.0);

  // playhead, normalized 0..1 of full track
  playhead = $state(0.0);
  playing  = $state(false);
  volume   = $state(0.7);    // 0..1

  // prompt + settings
  prompt = $state("");
  model  = $state("");          // current model dir id, synced from backend
  modelSwitching = $state(false);
  steps  = $state(8);
  cfg    = $state(1.0);
  noise  = $state(0.65);
  seed   = $state(-1);
  duration = $state(190);  // text-to-audio length (sec)

  loras = $state([]);

  generating  = $state(false);
  activityLog = $state([]);
  generationStatus = $state("");
  modelSwitchStatus = $state("");
  scrubbingNoise = $state(false);  // true while the user is actively dragging the A2A slider
  modelLoaded = $state(false);   // assume down until pollStats confirms otherwise
  stats = $state({ cpu: 0, vram: 0, ram: 0 });

  get latentCount() {
    return this.mask.length;
  }
  get paintedRanges() {
    return maskToRanges(this.mask);
  }
  get ghostRanges() {
    return maskToRanges(this.ghostMask);
  }
  get hasMask() {
    for (let i = 0; i < this.mask.length; i++) if (this.mask[i]) return true;
    return false;
  }

  setTrackInfo({ count, duration }) {
    this.trackSeconds = duration;
    // resize mask preserving existing values where possible
    const next = new Uint8Array(count);
    const old = this.mask;
    const lim = Math.min(old.length, count);
    for (let i = 0; i < lim; i++) next[i] = old[i];
    this.mask = next;
  }

  paint(startLatent, endLatent, mode) {
    if (endLatent < startLatent) [startLatent, endLatent] = [endLatent, startLatent];
    startLatent = Math.max(0, Math.floor(startLatent));
    endLatent = Math.min(this.mask.length, Math.ceil(endLatent));
    if (endLatent <= startLatent) return;
    const m = new Uint8Array(this.mask);
    const v = mode === "regen" ? 1 : 0;
    for (let i = startLatent; i < endLatent; i++) m[i] = v;
    this.mask = m;
  }

  clearMask() {
    this.mask = new Uint8Array(this.mask.length);
  }

  log(scope, message, detail = {}) {
    const entry = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      time: new Date(),
      scope,
      message,
      detail,
    };
    this.activityLog = [entry, ...this.activityLog].slice(0, 80);
    const prefix = scope === "model" ? "[model]" : scope === "generate" ? "[generate]" : "[frontend]";
    if (Object.keys(detail).length) console.log(prefix, message, detail);
    else console.log(prefix, message);
    return entry;
  }
}

export const session = new Session();


// ---------- backend api ----------

export async function apiState() {
  const r = await fetch("/api/state");
  const j = await r.json();
  session.hasAudio = j.has_audio;
  session.version = j.version;
  return j;
}

export async function apiUpload(file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/upload", { method: "POST", body: fd });
  if (!r.ok) throw new Error("upload failed: " + r.status);
  const j = await r.json();
  session.hasAudio = true;
  session.version = j.version;
  session.setTrackInfo(j);
  session.duration = Math.round(j.duration);   // sync length slider to the loaded sample
  return j;
}

export async function apiClear() {
  const r = await fetch("/api/clear", { method: "POST" });
  const j = await r.json();
  session.hasAudio = false;
  session.version = j.version;
  session.mask = new Uint8Array(0);
  session.ghostMask = new Uint8Array(0);
  session.trackSeconds = 0;
  return j;
}

export async function apiLibrary() {
  const r = await fetch("/api/library");
  if (!r.ok) throw new Error("library failed: " + r.status);
  return await r.json();
}

export async function apiLoadLibrary(id) {
  const r = await fetch("/api/library/load", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!r.ok) throw new Error("load failed: " + r.status);
  const j = await r.json();
  session.hasAudio = true;
  session.version = j.version;
  session.setTrackInfo(j);
  session.duration = Math.round(j.duration);
  session.playhead = 0;
  session.playing = false;
  return j;
}

export async function apiSaveToLibrary(label = "") {
  const r = await fetch("/api/library/save", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ label }),
  });
  if (!r.ok) throw new Error("save failed: " + r.status);
  return await r.json();
}

export async function apiDeleteLibrary(id) {
  const r = await fetch("/api/library/delete", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!r.ok) throw new Error("delete failed: " + r.status);
  return await r.json();
}

export async function apiModels() {
  const r = await fetch("/api/models");
  if (!r.ok) throw new Error("models list failed: " + r.status);
  return (await r.json()).models;  // [{id, label, current}]
}

export async function apiSwitchModel(id) {
  session.modelSwitching = true;
  session.modelSwitchStatus = `switching to ${id}`;
  session.log("model", `switch requested: ${id}`);
  try {
    session.log("model", "waiting for backend model loader");
    const r = await fetch("/api/switch-model", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: id }),
    });
    if (r.status === 409) {
      session.modelSwitchStatus = "blocked by active generation";
      session.log("model", "switch blocked: generation in progress");
      throw new Error("generation in progress");
    }
    if (!r.ok) throw new Error("switch failed: " + r.status);
    session.modelSwitchStatus = "backend response received";
    session.log("model", "backend returned switch response");
    const j = await r.json();
    session.model = j.model;
    session.modelSwitchStatus = `ready: ${j.label || j.model}`;
    session.log("model", `ready: ${j.label || j.model}`, { model: j.model });
    return j;
  } catch (e) {
    session.modelSwitchStatus = `failed: ${e.message}`;
    session.log("model", `switch failed: ${e.message}`);
    throw e;
  } finally {
    session.modelSwitching = false;
  }
}

let _genAbort = null;

export function cancelGenerate() {
  const hadActiveRequest = !!_genAbort || session.generating;
  if (_genAbort) _genAbort.abort();
  _genAbort = null;
  session.generating = false;
  if (hadActiveRequest) {
    session.generationStatus = "canceled";
    session.log("generate", "canceled");
  }
}

export async function apiGenerate() {
  cancelGenerate();
  session.generating = true;
  session.generationStatus = "preparing request";
  _genAbort = new AbortController();
  try {
    const mode = !session.hasAudio ? "text-to-audio" : session.hasMask ? "inpaint" : "vary";
    const regenLatents = Array.from(session.mask).reduce((n, v) => n + (v ? 1 : 0), 0);
    const body = {
      prompt: session.prompt,
      mask: Array.from(session.mask),
      settings: {
        steps: session.steps,
        cfg: session.cfg,
        seed: session.seed,
        noise: session.noise,
        duration: session.trackSeconds || session.duration,
      },
    };
    session.log("generate", `request prepared: ${mode}`, {
      steps: body.settings.steps,
      duration: body.settings.duration,
      regenLatents,
    });
    session.generationStatus = "waiting for backend";
    const r = await fetch("/api/generate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: _genAbort.signal,
    });
    if (r.status === 409) {
      session.generationStatus = "backend busy";
      session.log("generate", "backend busy with another generation");
      return null;
    }
    if (!r.ok) throw new Error("generate failed: " + r.status);
    session.generationStatus = "backend response received";
    session.log("generate", "backend returned generated audio metadata");
    const j = await r.json();
    session.generationStatus = "refreshing track";
    session.hasAudio = true;
    session.version = j.version;
    session.setTrackInfo(j);
    // remember the inpainted regions as ghost (visual recall), then clear the live mask
    if (body.mask.some(v => v)) {
      session.ghostMask = new Uint8Array(body.mask);
    }
    session.mask = new Uint8Array(session.mask.length);
    session.generationStatus = `ready: ${j.duration.toFixed(1)}s`;
    session.log("generate", "ready", { duration: j.duration, latents: j.count });
    return j;
  } catch (e) {
    if (e.name === "AbortError") {
      session.generationStatus = "canceled";
      return null;
    }
    session.generationStatus = `failed: ${e.message}`;
    session.log("generate", `failed: ${e.message}`);
    throw e;
  } finally {
    _genAbort = null;
    session.generating = false;
  }
}
