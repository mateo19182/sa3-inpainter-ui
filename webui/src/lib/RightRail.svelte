<script>
import { session, apiGenerate, cancelGenerate, apiModels, apiSwitchModel } from "./session.svelte.js";
import Panel from "./Panel.svelte";

let promptCharCount = $derived(session.prompt.length);
let recentActivity = $derived(session.activityLog.slice(0, 8));

function formatLogTime(t) {
  return t.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

let availableModels = $state([]);
async function refreshModels() {
  try { availableModels = await apiModels(); } catch (e) { console.warn("models list failed:", e); }
}
$effect(() => { refreshModels(); });

async function onModelChange(e) {
  const id = e.target.value;
  if (!id || id === session.model) return;
  try {
    await apiSwitchModel(id);
    await refreshModels();
  } catch (err) {
    alert("Model switch failed: " + err.message);
    e.target.value = session.model;  // revert select
  }
}

let ctaLabel = $derived.by(() => {
  if (!session.hasAudio) return "Generate";
  if (!session.hasMask) return session.noise > 0 ? "Vary" : "Generate";
  return "Inpaint";
});
let ctaVisible = $derived(true);

let loraLibrary = $state([]);
let loraDir = $state("");
let loraPickerOpen = $state(false);
let availableLoras = $derived(
  loraLibrary.filter(name => !session.loras.some(l => l.name === name))
);

async function refreshLoraLibrary() {
  try {
    const r = await fetch("/api/loras");
    const j = await r.json();
    loraLibrary = j.files || [];
    loraDir = j.dir || "";
  } catch (e) { console.warn("lora list failed:", e); }
}
$effect(() => { refreshLoraLibrary(); });

function addLora(name) {
  session.loras = [...session.loras, { name, strength: 0.7, enabled: true }];
  loraPickerOpen = false;
}

function togglePicker() {
  refreshLoraLibrary();
  loraPickerOpen = !loraPickerOpen;
}

function removeLora(i) {
  session.loras = session.loras.filter((_, j) => j !== i);
}

function rerollSeed() {
  session.seed = Math.floor(Math.random() * 1000000);
}

async function clickGenerate() {
  if (session.generating) { cancelGenerate(); return; }
  try { await apiGenerate(); } catch (e) { console.error(e); alert("generate failed: " + e.message); }
}
</script>

<aside class="right-rail">

  <section class="prompt-section">
    <header class="section-header"><span>Prompt</span></header>
    <textarea class="prompt-input" bind:value={session.prompt}></textarea>
    <div class="prompt-meta">
      <span class="text-muted">{promptCharCount} / 500</span>
      <button class="link" onclick={() => session.prompt = ""}>Clear</button>
    </div>
  </section>

  <section class="cta-panel">
    <button class="btn btn-primary btn-lg" onclick={clickGenerate}
            disabled={session.modelSwitching}>
      {#if session.modelSwitching}
        <i class="bi bi-arrow-repeat spin"></i> Loading model…
      {:else if session.generating}
        <i class="bi bi-stop-circle"></i> Cancel
      {:else}
        <i class="bi bi-magic"></i> {ctaLabel}
      {/if}
    </button>
  </section>

  <section class="activity-section">
    <header class="activity-header">
      <span>Progress</span>
      {#if session.generating}
        <span class="activity-state">{session.generationStatus || "generating"}</span>
      {:else if session.modelSwitching}
        <span class="activity-state">{session.modelSwitchStatus || "switching"}</span>
      {/if}
    </header>
    <div class="activity-log">
      {#each recentActivity as entry}
        <div class="activity-row" title={`${formatLogTime(entry.time)} ${entry.message}`}>
          <span class="activity-time">{formatLogTime(entry.time)}</span>
          <span class="activity-message">{entry.message}</span>
        </div>
      {:else}
        <div class="activity-empty">no progress events yet</div>
      {/each}
    </div>
  </section>

  <Panel title="Generation">
    {#snippet children()}
      <div class="form-row">
        <label>
          Model
          <span class="model-dot"
                class:ok={session.modelLoaded && !session.modelSwitching}
                class:switching={session.modelSwitching}
                title={session.modelSwitching ? "switching…" : session.modelLoaded ? "loaded" : "not loaded"}></span>
        </label>
        {#if session.modelSwitching}
          <span class="switching-label">Switching…</span>
        {:else}
          <select class="select" value={session.model} onchange={onModelChange}
                  disabled={session.modelSwitching || session.generating}>
            {#each availableModels as m}
              <option value={m.id}>{m.label}</option>
            {/each}
            {#if availableModels.length === 0}
              <option value={session.model}>{session.model || "Loading…"}</option>
            {/if}
          </select>
        {/if}
      </div>
      <!-- Length: only matters when generating from scratch (no source loaded) -->
      <div class="form-row" class:disabled={session.hasAudio}>
        <label>Length</label>
        <div class="slider-row">
          <input type="range" min="5" max="380" step="1" bind:value={session.duration} class="slider"
                 disabled={session.hasAudio}>
          <span class="value">{session.duration}s</span>
        </div>
      </div>
      <div class="form-row">
        <label>Steps</label>
        <div class="slider-row">
          <input type="range" min="1" max="32" bind:value={session.steps} class="slider">
          <span class="value">{session.steps}</span>
        </div>
      </div>
      <div class="form-row">
        <label>Guidance</label>
        <div class="slider-row">
          <input type="range" min="1" max="10" step="0.1" bind:value={session.cfg} class="slider">
          <span class="value">{session.cfg.toFixed(1)}</span>
        </div>
      </div>
      <!-- A2A strength: always visible, greyed when inpainting (mask present) or no source.
           when inpainting, value displays as 0 (not applied) without losing the saved setting. -->
      <div class="form-row" class:disabled={!session.hasAudio || session.hasMask}>
        <label>A2A</label>
        <div class="slider-row">
          {#if session.hasMask}
            <input type="range" min="0" max="1" step="0.01" value="0" class="slider" disabled>
            <span class="value">0.00</span>
          {:else}
            <input type="range" min="0" max="1" step="0.01" bind:value={session.noise} class="slider"
                   disabled={!session.hasAudio}
                   onpointerdown={() => session.scrubbingNoise = true}
                   onpointerup={() => session.scrubbingNoise = false}>
            <span class="value">{session.noise.toFixed(2)}</span>
          {/if}
        </div>
      </div>
      <div class="form-row">
        <label>Seed</label>
        <div class="seed-row">
          <input type="text" bind:value={session.seed} class="seed-input">
          <button class="icon-btn" onclick={rerollSeed} title="Random seed">
            <i class="bi bi-dice-5"></i>
          </button>
        </div>
      </div>
    {/snippet}
  </Panel>

  <section class="loras-section">
    <header class="loras-header">
      <span>LoRAs</span>
      <button class="icon-btn" onclick={togglePicker} title="Add LoRA">
        <i class="bi bi-plus"></i>
      </button>
    </header>
    {#if loraPickerOpen}
      <div class="lora-picker">
        {#if availableLoras.length}
          {#each availableLoras as name}
            <button class="picker-item" onclick={() => addLora(name)}>{name}</button>
          {/each}
        {:else}
          <span class="picker-empty">no loras in {loraDir || "library"}</span>
        {/if}
      </div>
    {/if}
    <div class="loras-box">
      {#each session.loras as lora, i}
        <div class="lora-card">
          <div class="lora-head">
            <span class="lora-name">{lora.name}</span>
            <button class="icon-btn" onclick={() => removeLora(i)}>
              <i class="bi bi-x"></i>
            </button>
          </div>
          <div class="slider-row">
            <input type="range" min="0" max="1" step="0.01" bind:value={lora.strength} class="slider">
            <span class="value">{lora.strength.toFixed(2)}</span>
          </div>
        </div>
      {:else}
        <div class="lora-empty">drop a .safetensors here or click + to add</div>
      {/each}
    </div>
  </section>

</aside>

<style>
.right-rail {
  background: var(--bg-lighter);
  border-left: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}
.form-row {
  display: grid;
  grid-template-columns: 70px 1fr;
  align-items: center;
  gap: var(--gap-3);
}
.form-row label {
  font-size: 11px;
  color: var(--text-secondary);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.form-row.disabled label,
.form-row.disabled .value {
  color: var(--text-muted);
}
.model-dot {
  display: inline-block;
  margin-left: 6px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--error-red);
  vertical-align: middle;
}
.model-dot.ok { background: var(--success-green); }
.model-dot.switching { background: var(--accent-blue); animation: pulse 1s ease-in-out infinite; }
.switching-label { font-size: 12px; color: var(--text-muted); font-style: italic; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
@keyframes spin { to { transform: rotate(360deg); } }
.spin { display: inline-block; animation: spin 1s linear infinite; }
.form-row.disabled .slider { opacity: 0.4; pointer-events: none; }
.prompt-section {
  padding: 0 var(--gap-4) var(--gap-2);
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.section-header {
  padding: var(--gap-3) 0;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-primary);
}
.prompt-input {
  background: var(--code-block);
  border: 1px solid var(--border-color);
  color: var(--text-primary);
  padding: var(--gap-3);
  resize: vertical;
  min-height: 64px;
  font-size: 13px;
  width: 100%;
}
.prompt-input:focus { outline: 1px solid var(--accent-blue); border-color: transparent; }
.prompt-meta { display: flex; justify-content: space-between; align-items: center; font-size: 11px; }
.link { color: var(--accent-blue); font-size: 11px; }
.link:hover { color: var(--text-primary); }

.slider-row { display: flex; align-items: center; gap: var(--gap-3); }
.slider-row .value {
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  color: var(--text-primary);
  min-width: 32px;
  text-align: right;
}
.select, .seed-input {
  background: var(--code-block);
  border: 1px solid var(--border-color);
  color: var(--text-primary);
  padding: 6px var(--gap-2);
  font-size: 12px;
  appearance: none;
  width: 100%;
}
.seed-input {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-variant-numeric: tabular-nums;
  flex: 1;
  padding: 4px var(--gap-2);
}
.seed-row { display: flex; align-items: center; gap: var(--gap-1); }

.loras-section {
  padding: 0 var(--gap-4) var(--gap-2);
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.loras-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--gap-3) 0;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-primary);
}
.loras-box {
  border: 1px dashed var(--border-color);
  border-radius: 4px;
  padding: var(--gap-2);
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
  min-height: 56px;
}
.lora-empty {
  color: var(--text-muted);
  font-size: 11px;
  text-align: center;
  padding: var(--gap-3);
  font-style: italic;
}
.lora-card {
  background: var(--code-block);
  border: 1px solid var(--border-color);
  border-radius: 3px;
  padding: var(--gap-2) var(--gap-3);
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.lora-head { display: flex; justify-content: space-between; align-items: center; }
.lora-name { font-size: 12px; color: var(--text-primary); }

.lora-picker {
  background: var(--code-block);
  border: 1px solid var(--border-color);
  max-height: 200px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}
.picker-item {
  text-align: left;
  padding: 6px var(--gap-3);
  font-size: 12px;
  color: var(--text-primary);
  background: transparent;
  border: 0;
}
.picker-item:hover { background: var(--code-highlight); color: var(--accent-blue); }
.picker-empty {
  padding: 6px var(--gap-3);
  font-size: 11px;
  color: var(--text-muted);
  font-style: italic;
}

.cta-panel {
  display: flex;
  gap: var(--gap-2);
  padding: 0 var(--gap-4) var(--gap-4);
  align-items: stretch;
}
.cta-panel .btn-primary { height: 36px; padding: 0 var(--gap-3); border-radius: 4px; border: 0; }
.cta-panel .btn-square { border-radius: 4px; }
.variant-nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--gap-3);
  padding: var(--gap-3);
  border-bottom: 1px solid var(--border-color);
}
.variant-count {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--text-secondary);
  min-width: 48px;
  text-align: center;
}
.activity-section {
  padding: 0 var(--gap-4) var(--gap-4);
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.activity-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-primary);
}
.activity-state {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--accent-blue);
  font-size: 11px;
  font-weight: 400;
}
.activity-log {
  border: 1px solid var(--border-color);
  background: var(--code-block);
  min-height: 44px;
  max-height: 148px;
  overflow-y: auto;
}
.activity-row {
  display: grid;
  grid-template-columns: 56px 1fr;
  gap: var(--gap-2);
  align-items: baseline;
  padding: 5px var(--gap-2);
  border-bottom: 1px solid var(--border-color);
  font-size: 11px;
}
.activity-row:hover {
  background: var(--code-highlight);
}
.activity-row:last-child { border-bottom: 0; }
.activity-time {
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.activity-message {
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.activity-row:hover .activity-message {
  overflow: visible;
  text-overflow: clip;
  white-space: normal;
  word-break: break-word;
}
.activity-empty {
  padding: var(--gap-3);
  color: var(--text-muted);
  font-size: 11px;
  font-style: italic;
  text-align: center;
}
</style>
