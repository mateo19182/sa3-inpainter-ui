<script>
import { session, apiLibrary, apiLoadLibrary, apiSaveToLibrary, apiDeleteLibrary } from "./session.svelte.js";
import { fmtTime } from "./util.js";

let { open = false, onClose = () => {} } = $props();

let items = $state([]);
let libraryDir = $state("");
let query = $state("");
let tab = $state("all");
let busyId = $state("");
let saving = $state(false);
let error = $state("");

let filteredItems = $derived.by(() => {
  const q = query.trim().toLowerCase();
  return items.filter((item) => {
    if (tab !== "all" && item.kind !== tab) return false;
    if (!q) return true;
    return `${item.name} ${item.filename} ${item.kind}`.toLowerCase().includes(q);
  });
});

$effect(() => {
  if (open) refresh();
});

function cleanName(item) {
  return (item.name || item.filename || "untitled")
    .replace(/^\d{8}-\d{6}-/, "")
    .replaceAll("-", " ");
}

function kindLabel(kind) {
  return kind === "generation" ? "Generation" : "Audio";
}

function formatCreated(seconds) {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatBytes(bytes) {
  if (!bytes) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(bytes > 10 * 1024 * 1024 ? 0 : 1)} MB`;
}

async function refresh() {
  error = "";
  try {
    const j = await apiLibrary();
    items = j.items || [];
    libraryDir = j.dir || "";
  } catch (e) {
    error = e.message;
  }
}

async function loadItem(item) {
  busyId = item.id;
  error = "";
  try {
    await apiLoadLibrary(item.id);
    onClose();
  } catch (e) {
    error = e.message;
  } finally {
    busyId = "";
  }
}

async function saveCurrent() {
  saving = true;
  error = "";
  try {
    await apiSaveToLibrary(session.prompt || "saved-generation");
    await refresh();
  } catch (e) {
    error = e.message;
  } finally {
    saving = false;
  }
}

async function deleteItem(item) {
  busyId = item.id;
  error = "";
  try {
    await apiDeleteLibrary(item.id);
    items = items.filter((entry) => entry.id !== item.id);
  } catch (e) {
    error = e.message;
  } finally {
    busyId = "";
  }
}
</script>

{#if open}
  <div class="library-backdrop" role="presentation" onclick={onClose}></div>
  <section class="library-explorer" aria-label="Library explorer">
    <header class="library-header">
      <div>
        <h2>Library</h2>
        <span title={libraryDir}>{items.length} items</span>
      </div>
      <div class="header-actions">
        <button class="icon-btn" onclick={refresh} title="Refresh">
          <i class="bi bi-arrow-clockwise"></i>
        </button>
        <button class="icon-btn" onclick={onClose} title="Close">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>
    </header>

    <div class="library-tools">
      <div class="tabs" role="tablist" aria-label="Library type">
        <button class:active={tab === "all"} onclick={() => tab = "all"}>All</button>
        <button class:active={tab === "audio"} onclick={() => tab = "audio"}>Audio</button>
        <button class:active={tab === "generation"} onclick={() => tab = "generation"}>Generations</button>
      </div>
      <div class="search">
        <i class="bi bi-search"></i>
        <input type="search" placeholder="Search" bind:value={query}>
      </div>
    </div>

    <div class="save-row">
      <button class="btn btn-ghost" onclick={saveCurrent} disabled={!session.hasAudio || saving}>
        <i class="bi {saving ? 'bi-hourglass-split' : 'bi-bookmark-plus'}"></i>
        {saving ? "Saving" : "Save current"}
      </button>
    </div>

    {#if error}
      <div class="library-error">{error}</div>
    {/if}

    <div class="item-list">
      {#each filteredItems as item (item.id)}
        <article class="library-item">
          <button class="item-main" onclick={() => loadItem(item)} disabled={busyId === item.id}>
            <span class="item-icon">
              <i class="bi {item.kind === 'generation' ? 'bi-stars' : 'bi-music-note-beamed'}"></i>
            </span>
            <span class="item-copy">
              <span class="item-title">{cleanName(item)}</span>
              <span class="item-meta">
                {kindLabel(item.kind)} - {fmtTime(item.duration || 0)} - {formatBytes(item.bytes)} - {formatCreated(item.created)}
              </span>
            </span>
          </button>
          <div class="item-actions">
            <button class="icon-btn" onclick={() => loadItem(item)} disabled={busyId === item.id} title="Load">
              <i class="bi bi-box-arrow-in-down"></i>
            </button>
            <button class="icon-btn" onclick={() => deleteItem(item)} disabled={busyId === item.id} title="Delete">
              <i class="bi bi-trash3"></i>
            </button>
          </div>
        </article>
      {:else}
        <div class="empty-state">No items</div>
      {/each}
    </div>
  </section>
{/if}

<style>
.library-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.52);
  z-index: 180;
}
.library-explorer {
  position: fixed;
  top: calc(var(--topbar-h) + var(--gap-3));
  right: var(--gap-3);
  width: min(520px, calc(100vw - 24px));
  max-height: calc(100vh - var(--topbar-h) - var(--bottombar-h) - 24px);
  display: grid;
  grid-template-rows: auto auto auto auto 1fr;
  background: var(--bg-lighter);
  border: 1px solid var(--border-color);
  z-index: 181;
  box-shadow: 0 18px 70px rgba(0, 0, 0, 0.42);
}
.library-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--gap-4);
  border-bottom: 1px solid var(--border-color);
}
.library-header h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0;
}
.library-header span {
  display: block;
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 11px;
}
.header-actions {
  display: flex;
  gap: var(--gap-1);
}
.library-tools {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--gap-3);
  align-items: center;
  padding: var(--gap-3) var(--gap-4);
  border-bottom: 1px solid var(--border-color);
}
.tabs {
  display: flex;
  border: 1px solid var(--border-color);
  background: var(--code-block);
}
.tabs button {
  min-width: 56px;
  height: 28px;
  padding: 0 var(--gap-3);
  color: var(--text-secondary);
  font-size: 12px;
}
.tabs button + button {
  border-left: 1px solid var(--border-color);
}
.tabs button:hover,
.tabs button.active {
  color: var(--text-primary);
  background: var(--code-highlight);
}
.search {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
  height: 30px;
  min-width: 0;
  padding: 0 var(--gap-2);
  background: var(--code-block);
  border: 1px solid var(--border-color);
  color: var(--text-muted);
}
.search input {
  min-width: 0;
  width: 100%;
  background: transparent;
  border: 0;
  outline: 0;
  font-size: 12px;
  color: var(--text-primary);
}
.save-row {
  display: flex;
  justify-content: flex-end;
  padding: var(--gap-2) var(--gap-4);
  border-bottom: 1px solid var(--border-color);
}
.save-row .btn[disabled] {
  color: var(--text-muted);
  cursor: default;
}
.save-row .btn[disabled]:hover {
  color: var(--text-muted);
  background: transparent;
}
.library-error {
  color: var(--error-red);
  font-size: 12px;
  padding: var(--gap-2) var(--gap-4);
  border-bottom: 1px solid var(--border-color);
}
.item-list {
  overflow: auto;
  min-height: 180px;
}
.library-item {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  min-height: 58px;
  border-bottom: 1px solid var(--border-color);
}
.item-main {
  min-width: 0;
  display: grid;
  grid-template-columns: 32px 1fr;
  align-items: center;
  gap: var(--gap-3);
  padding: var(--gap-2) var(--gap-4);
  text-align: left;
}
.item-main:hover {
  background: var(--code-highlight);
}
.item-main[disabled] {
  cursor: default;
  opacity: 0.6;
}
.item-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  color: var(--text-secondary);
  background: var(--code-block);
  border: 1px solid var(--border-color);
}
.item-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.item-title {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.item-meta {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 11px;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.item-actions {
  display: flex;
  gap: var(--gap-1);
  padding-right: var(--gap-3);
}
.empty-state {
  padding: var(--gap-5) var(--gap-4);
  color: var(--text-muted);
  text-align: center;
  font-size: 12px;
}
@media (max-width: 620px) {
  .library-explorer {
    left: var(--gap-2);
    right: var(--gap-2);
    width: auto;
  }
  .library-tools {
    grid-template-columns: 1fr;
  }
  .tabs button {
    flex: 1;
  }
}
</style>
