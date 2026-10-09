<!--
  目录选择器。手动输入路径容易打错一个字符就扫了个空目录，
  所以给一个能点着走的选择框。
-->
<script lang="ts">
  import { api } from '../lib/api'
  import type { BrowseResult } from '../lib/types'

  interface Props {
    title: string
    initial?: string
    onpick: (path: string) => void
    onclose: () => void
  }

  let { title, initial, onpick, onclose }: Props = $props()

  let data = $state<BrowseResult | null>(null)
  let manual = $state('')
  let error = $state('')
  let loading = $state(false)

  async function go(path?: string) {
    loading = true
    error = ''
    try {
      const result = await api.browse(path)
      data = result
      manual = result.path
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  // 打开时定位到传入的目录；它不存在就退回家目录
  $effect(() => {
    go(initial)
  })

  function confirm() {
    if (data) onpick(data.path)
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose()
  }

  // 点遮罩关闭，点弹窗本体不关。用 target 判定，省掉内层 stopPropagation
  function onBackdrop(e: MouseEvent) {
    if (e.target === e.currentTarget) onclose()
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="backdrop" role="presentation" onclick={onBackdrop}>
  <div class="modal" role="dialog" aria-modal="true" aria-label={title} tabindex="-1">
    <header>
      <h3>{title}</h3>
      <button class="ghost" onclick={onclose} aria-label="关闭">✕</button>
    </header>

    <div class="pathbar">
      <button onclick={() => go(data?.parent ?? undefined)} disabled={!data?.parent || loading}>
        ↑ 上一层
      </button>
      <input
        type="text"
        bind:value={manual}
        onkeydown={(e) => e.key === 'Enter' && go(manual)}
        placeholder="/Users/…"
      />
      <button onclick={() => go(manual)} disabled={loading}>前往</button>
    </div>

    {#if error}
      <div class="banner error">{error}</div>
    {/if}

    <div class="list">
      {#if loading}
        <p class="muted empty">读取中…</p>
      {:else if data && data.subdirs.length === 0}
        <p class="muted empty">这个目录下没有子目录</p>
      {:else if data}
        {#each data.subdirs as dir (dir.path)}
          <button class="row" onclick={() => go(dir.path)}>
            <span class="icon">📁</span>
            <span class="name">{dir.name}</span>
          </button>
        {/each}
      {/if}
    </div>

    <footer>
      <span class="muted mono current">{data?.path ?? ''}</span>
      <button class="primary" onclick={confirm} disabled={!data}>选择此目录</button>
    </footer>
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.42);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    z-index: 50;
  }

  .modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    width: min(620px, 100%);
    max-height: min(640px, 90vh);
    display: flex;
    flex-direction: column;
    padding: 16px 18px;
  }

  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }

  .pathbar {
    display: flex;
    gap: 8px;
    margin-bottom: 10px;
  }

  .pathbar input {
    flex: 1;
  }

  .list {
    flex: 1;
    overflow-y: auto;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    min-height: 220px;
    background: var(--surface-2);
  }

  .row {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    text-align: left;
    border: none;
    border-bottom: 1px solid var(--border);
    border-radius: 0;
    background: transparent;
    padding: 8px 12px;
  }

  .row:last-child {
    border-bottom: none;
  }

  .icon {
    flex: none;
  }

  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .empty {
    padding: 20px;
    text-align: center;
  }

  footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-top: 12px;
  }

  .current {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text-dim);
  }
</style>
