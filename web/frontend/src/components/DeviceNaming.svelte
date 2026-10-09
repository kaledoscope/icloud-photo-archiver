<!--
  设备命名。扫描完发现有些文件归到了认不出的文件夹，在这里补上名字，
  写回 config/device-models.json，下次扫描就自动归类。

  两类需要处理，原因不同：
    · 映射表里没有的机型（如 "Xiaomi 14 Ultra" → Xiaomi_14_Ultra）
      —— 文件夹名是按型号拼的，能读出来，只是不好认
    · 读不出机型（元数据里压根没有设备信息）
      —— 只能给这一整批指定一个统一名称，因为无从区分谁是谁
-->
<script lang="ts">
  import { api } from '../lib/api'
  import type { ModelsResult } from '../lib/types'

  interface Props {
    /** 本次计划里出现过的设备文件夹名 */
    planDevices: string[]
    onclose: () => void
    onchanged: () => void
  }

  let { planDevices, onclose, onchanged }: Props = $props()

  let models = $state<ModelsResult | null>(null)
  let error = $state('')
  let saved = $state('')

  // 待命名设备的编辑态。在 load() 里一次性建好，不在渲染期改 state
  // ——Svelte 5 不允许在渲染过程中写状态。
  let drafts = $state<Record<string, { folder_name: string; owner: string }>>({})
  let unknownDraft = $state('')

  async function load() {
    try {
      const result = await api.models()
      models = result
      unknownDraft = result.unknown_label

      const known = new Set(result.entries.map((e) => e.folder_name))
      const next: typeof drafts = {}
      for (const device of planDevices) {
        if (known.has(device) || device === result.unknown_label) continue
        // 保留用户已经敲进去的内容，只在首次出现时给默认值
        next[device] = drafts[device] ?? { folder_name: device, owner: '' }
      }
      drafts = next
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  $effect(() => {
    load()
  })

  // 计划里出现过、且映射表里已有的设备，只做展示
  let named = $derived((models?.entries ?? []).filter((e) => planDevices.includes(e.folder_name)))
  let unmapped = $derived(Object.keys(drafts))
  let needsUnknown = $derived(planDevices.includes(models?.unknown_label ?? ''))

  async function save(device: string) {
    error = ''
    saved = ''
    const d = drafts[device]
    try {
      await api.saveModel({
        exif_name: device,
        folder_name: d.folder_name,
        owner: d.owner,
      })
      saved = `已记下：${device} → ${d.folder_name}`
      await load()
      onchanged()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  async function saveUnknown() {
    error = ''
    saved = ''
    try {
      const r = await api.saveUnknownFolder(unknownDraft)
      saved = r.is_default
        ? '已恢复默认：读不出机型的文件归入 Unknown_Device'
        : `已记下：读不出机型的文件归入 ${r.folder_name}`
      await load()
      onchanged()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose()
  }

  function onBackdrop(e: MouseEvent) {
    if (e.target === e.currentTarget) onclose()
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="backdrop" role="presentation" onclick={onBackdrop}>
  <div class="modal" role="dialog" aria-modal="true" aria-label="设备命名" tabindex="-1">
    <header>
      <h3>设备命名</h3>
      <button class="ghost" onclick={onclose} aria-label="关闭">✕</button>
    </header>

    <p class="muted intro">
      命名会写回 <code>{models?.path ?? 'config/device-models.json'}</code>，只影响文件夹叫什么名字，
      不动任何照片文件。
    </p>

    {#if error}<div class="banner error">{error}</div>{/if}
    {#if saved}<div class="banner ok">{saved}</div>{/if}

    <div class="body">
      {#if unmapped.length > 0}
        <section>
          <h4>认不出的机型</h4>
          <p class="muted small">元数据里读到了型号，只是不在映射表里。给它们起个好认的名字：</p>
          {#each unmapped as device (device)}
            <div class="item">
              <div class="raw mono" title="元数据里读到的原始值">{device}</div>
              <div class="inputs">
                <input type="text" bind:value={drafts[device].folder_name} placeholder="文件夹名" />
                <input
                  type="text"
                  class="owner"
                  bind:value={drafts[device].owner}
                  placeholder="谁的（可留空）"
                />
                <button onclick={() => save(device)}>保存</button>
              </div>
            </div>
          {/each}
        </section>
      {/if}

      {#if needsUnknown}
        <section>
          <h4>读不出机型的文件</h4>
          <p class="muted small">
            这批文件的元数据里没有设备信息，无法区分谁是谁。如果它们都来自同一台设备，
            给一个统一的名字；不确定就留空，维持 <code>{models?.default_unknown_label}</code>。
          </p>
          <div class="item">
            <div class="inputs">
              <input type="text" bind:value={unknownDraft} placeholder="文件夹名，留空恢复默认" />
              <button onclick={saveUnknown}>保存</button>
            </div>
          </div>
        </section>
      {/if}

      {#if unmapped.length === 0 && !needsUnknown}
        <p class="muted center">
          本次计划里的设备都在映射表里，没有需要命名的。
        </p>
      {/if}

      {#if named.length > 0}
        <section>
          <h4>本次用到的已有映射</h4>
          <table>
            <thead>
              <tr><th>原始机型</th><th>文件夹</th><th>归属</th></tr>
            </thead>
            <tbody>
              {#each named as e (e.exif_name)}
                <tr>
                  <td class="mono">{e.exif_name}</td>
                  <td class="mono">{e.folder_name}</td>
                  <td class="muted">{e.owner || '—'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </section>
      {/if}
    </div>

    <footer>
      <button class="primary" onclick={onclose}>完成</button>
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
    width: min(680px, 100%);
    max-height: min(700px, 92vh);
    display: flex;
    flex-direction: column;
    padding: 16px 18px;
  }

  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }

  .intro {
    font-size: 12.5px;
    margin: 0 0 10px;
  }

  .body {
    flex: 1;
    overflow-y: auto;
    padding-right: 4px;
  }

  section {
    margin-bottom: 20px;
  }

  h4 {
    font-size: 13px;
    margin: 0 0 4px;
  }

  .small {
    font-size: 12.5px;
    margin: 0 0 8px;
  }

  .item {
    background: var(--surface-2);
    border-radius: var(--radius);
    padding: 10px 12px;
    margin-bottom: 8px;
  }

  .raw {
    color: var(--text-dim);
    margin-bottom: 6px;
    word-break: break-all;
  }

  .inputs {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .inputs input {
    flex: 1 1 180px;
    min-width: 0;
  }

  .inputs input.owner {
    flex: 0 1 130px;
  }

  .inputs button {
    flex: none;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }

  th {
    text-align: left;
    color: var(--text-dim);
    font-weight: 500;
    padding: 5px 8px;
    border-bottom: 1px solid var(--border);
  }

  td {
    padding: 5px 8px;
    border-bottom: 1px solid var(--border);
    word-break: break-all;
  }

  .center {
    text-align: center;
    padding: 24px;
  }

  footer {
    display: flex;
    justify-content: flex-end;
    padding-top: 12px;
  }

  .banner.ok {
    background: var(--ok-soft);
    color: var(--ok);
    border-color: var(--ok);
  }
</style>
