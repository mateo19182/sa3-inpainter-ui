<script>
import { session, apiUpload, apiClear, apiSaveToLibrary } from "./session.svelte.js";
import LibraryExplorer from "./LibraryExplorer.svelte";

let fileInput = $state(null);
let libraryOpen = $state(false);
let saveState = $state("");
let saveTimer = 0;

async function onFile(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  await apiUpload(file);
  fileInput.value = "";
}

async function onClear() {
  await apiClear();
}

async function onSave() {
  if (!session.hasAudio) return;
  saveState = "saving";
  try {
    await apiSaveToLibrary(session.prompt || "saved-generation");
    saveState = "saved";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveState = "", 1400);
  } catch (e) {
    saveState = "error";
    console.error(e);
  }
}
</script>

<header class="topbar">
  <div class="brand">
    <i class="bi bi-soundwave brand-icon"></i>
    <span class="brand-name">Audio Inpainter</span>
  </div>
  <div class="topbar-actions">
    <button class="btn btn-ghost" onclick={() => libraryOpen = true}>
      <i class="bi bi-collection"></i> Library
    </button>
    <button class="btn btn-ghost" onclick={() => fileInput.click()}>
      <i class="bi bi-folder2-open"></i> Load
    </button>
    <button class="btn btn-ghost" onclick={onSave} disabled={!session.hasAudio}>
      <i class="bi {saveState === 'saving' ? 'bi-hourglass-split' : saveState === 'saved' ? 'bi-check2' : 'bi-bookmark-plus'}"></i>
      {saveState === "saved" ? "Saved" : "Save"}
    </button>
    <button class="btn btn-ghost" onclick={onClear}>
      <i class="bi bi-file-earmark-plus"></i> New
    </button>
    <button class="btn btn-ghost" onclick={onClear}>
      <i class="bi bi-x-lg"></i> Clear
    </button>
    <input type="file" accept="audio/*,.wav,.mp3,.flac"
           bind:this={fileInput} onchange={onFile} style="display: none" />
  </div>
</header>

<LibraryExplorer open={libraryOpen} onClose={() => libraryOpen = false} />

<style>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--gap-4);
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-dark);
}
.brand { display: flex; align-items: center; gap: var(--gap-2); }
.brand-icon { color: var(--accent-blue); font-size: 18px; }
.brand-name { font-size: 14px; font-weight: 500; letter-spacing: 0.01em; }
.topbar-actions { display: flex; gap: var(--gap-1); }
.topbar-actions .btn[disabled] { color: var(--text-muted); cursor: default; }
.topbar-actions .btn[disabled]:hover { color: var(--text-muted); background: transparent; }
</style>
