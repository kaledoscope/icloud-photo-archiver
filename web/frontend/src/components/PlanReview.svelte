<!--
  计划审阅。执行复制之前，得让人看清楚「哪些文件、去哪儿、有没有可疑的」。
  可疑的主要是两类：读不出机型的（Unknown_Device），和没有拍摄时间的（用了 mtime）。
-->
<script lang="ts">
  import { api } from '../lib/api'
  import type { Plan, PlanSummary } from '../lib/types'
  import { baseName, humanSize, kindLabel, parentOf, shortTime } from '../lib/format'

  interface Props {
    planId: string
    summary: PlanSummary
    inputDir: string
    outputDir: string
    unknownLabel: string
    onNameDevice: (device: string) => void
  }

  let { planId, summary, inputDir, outputDir, unknownLabel, onNameDevice }: Props = $props()

  const PAGE = 100

  let page = $state<Plan | null>(null)
  let offset = $state(0)
  let device = $state('')
  let kind = $state('')
  let loading = $state(false)
  let error = $state('')

  async function load() {
    loading = true
    error = ''
    try {
      page = await api.plan(planId, {
        offset,
        limit: PAGE,
        device: device || undefined,
        kind: kind || undefined,
      })
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  // 过滤条件变化时回到第一页，否则会停在一个空页上
  $effect(() => {
    device
    kind
    offset
    load()
  })

  function setDevice(value: string) {
    device = device === value ? '' : value
    offset = 0
  }

  function setKind(value: string) {
    kind = kind === value ? '' : value
    offset = 0
  }

  let devices = $derived(
    Object.entries(summary.by_device).sort(
      (a, b) => b[1].photos + b[1].videos - (a[1].photos + a[1].videos),
    ),
  )

  let unknownCount = $derived(
    summary.by_device[unknownLabel]
      ? summary.by_device[unknownLabel].photos + summary.by_device[unknownLabel].videos
      : 0,
  )

  let mtimeCount = $derived(summary.by_date_source.mtime ?? 0)

  let maxPage = $derived(page ? Math.max(1, Math.ceil(page.total / PAGE)) : 1)
  let curPage = $derived(page ? Math.floor(page.offset / PAGE) + 1 : 1)
</script>

<div class="paths">
  <div><span class="k">源</span><span class="mono v" title={inputDir}>{inputDir}</span></div>
  <div><span class="k">→</span><span class="mono v" title={outputDir}>{outputDir}</span></div>
</div>

<div class="stats">
  <div class="stat">
    <div class="n">{summary.files}</div>
    <div class="l">将复制文件</div>
  </div>
  <div class="stat">
    <div class="n">{humanSize(summary.total_bytes)}</div>
    <div class="l">总大小</div>
  </div>
  <div class="stat">
    <div class="n">{summary.skipped}</div>
    <div class="l">跳过</div>
  </div>
  <div class="stat">
    <div class="n">{devices.length}</div>
    <div class="l">设备数</div>
  </div>
</div>

{#if unknownCount > 0}
  <div class="banner warn">
    <strong>{unknownCount} 个文件读不出机型</strong>，会归到 <code>{unknownLabel}/</code>。
    如果知道它们是什么设备，现在命名，下次扫描就会自动归类。
    <button class="linklike" onclick={() => onNameDevice(unknownLabel)}>去命名 →</button>
  </div>
{/if}

{#if mtimeCount > 0}
  <div class="banner warn">
    {mtimeCount} 个文件没有拍摄时间，用了文件修改时间兜底——这些文件的年月可能不准。
  </div>
{/if}

<h3 class="sub">按设备分布</h3>
<div class="devices">
  {#each devices as [name, c] (name)}
    <button
      class="dev"
      class:active={device === name}
      onclick={() => setDevice(name)}
      title="点击只看这台设备"
    >
      <div class="devname">{name}</div>
      <div class="devcounts">
        {#if c.photos}<span class="pill">照片 {c.photos}</span>{/if}
        {#if c.videos}<span class="pill">视频 {c.videos}</span>{/if}
        {#if c.sidecars}<span class="pill">旁车 {c.sidecars}</span>{/if}
      </div>
    </button>
  {/each}
</div>

<div class="filters">
  <span class="muted">筛选：</span>
  {#each [['photo', '照片'], ['video', '视频'], ['sidecar', '旁车']] as [value, label] (value)}
    <button class="chip" class:active={kind === value} onclick={() => setKind(value)}>{label}</button>
  {/each}
  {#if device || kind}
    <button
      class="chip clear"
      onclick={() => {
        device = ''
        kind = ''
        offset = 0
      }}>清除筛选</button
    >
  {/if}
  <span class="spacer"></span>
  <span class="muted">{page?.total ?? 0} 条</span>
</div>

{#if error}
  <div class="banner error">{error}</div>
{/if}

<div class="tablewrap">
  <table>
    <thead>
      <tr>
        <th>目标路径</th>
        <th class="num">大小</th>
        <th>拍摄时间</th>
      </tr>
    </thead>
    <tbody>
      {#if loading && !page}
        <tr><td colspan="3" class="muted center">读取中…</td></tr>
      {:else if page && page.total === 0}
        <tr><td colspan="3" class="muted center">没有符合条件的文件</td></tr>
      {:else if page}
        {#each page.entries as e (e.src + e.rel)}
          <tr>
            <td>
              <div class="mono parent">{parentOf(e.rel)}/</div>
              <div class="mono fname">
                {baseName(e.rel)}
                <span class="pill tiny">{kindLabel(e.kind)}</span>
                {#if e.date_source === 'mtime'}<span class="pill tiny warn">时间存疑</span>{/if}
              </div>
            </td>
            <td class="num">{humanSize(e.size_bytes)}</td>
            <td class="nowrap">{shortTime(e.taken_at)}</td>
          </tr>
        {/each}
      {/if}
    </tbody>
  </table>
</div>

{#if page && maxPage > 1}
  <div class="pager">
    <button disabled={offset === 0} onclick={() => (offset = Math.max(0, offset - PAGE))}>
      上一页
    </button>
    <span class="muted">第 {curPage} / {maxPage} 页</span>
    <button
      disabled={offset + PAGE >= page.total}
      onclick={() => (offset = offset + PAGE)}>下一页</button
    >
  </div>
{/if}

<style>
  .paths {
    background: var(--surface-2);
    border-radius: var(--radius);
    padding: 9px 12px;
    margin-bottom: 14px;
    font-size: 12px;
    line-height: 1.7;
  }

  .paths > div {
    display: flex;
    gap: 8px;
    min-width: 0;
  }

  .paths .k {
    color: var(--text-dim);
    flex: none;
    width: 12px;
    text-align: center;
  }

  .paths .v {
    /* 路径太长就折行，不要用 direction:rtl 去省开头——
       那会把开头的 "/" 挪到末尾，看起来像路径本身写错了 */
    word-break: break-all;
  }

  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 12px;
    margin-bottom: 14px;
  }

  .stat {
    background: var(--surface-2);
    border-radius: var(--radius);
    padding: 10px 14px;
  }

  .stat .n {
    font-size: 20px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .stat .l {
    font-size: 12px;
    color: var(--text-dim);
  }

  .sub {
    font-size: 13px;
    color: var(--text-dim);
    margin: 16px 0 8px;
  }

  .devices {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
    gap: 10px;
    margin-bottom: 16px;
  }

  .dev {
    text-align: left;
    padding: 10px 12px;
  }

  .dev.active {
    border-color: var(--accent);
    background: var(--accent-soft);
  }

  .devname {
    font-weight: 500;
    margin-bottom: 5px;
    word-break: break-all;
  }

  .devcounts {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .filters {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
    flex-wrap: wrap;
  }

  .spacer {
    flex: 1;
  }

  .chip {
    padding: 3px 11px;
    border-radius: 20px;
    font-size: 12.5px;
  }

  .chip.active {
    border-color: var(--accent);
    background: var(--accent-soft);
    color: var(--accent);
  }

  .chip.clear {
    color: var(--danger);
  }

  .tablewrap {
    max-height: 420px;
    overflow-y: auto;
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }

  table {
    width: 100%;
    border-collapse: collapse;
  }

  th {
    position: sticky;
    top: 0;
    background: var(--surface-2);
    text-align: left;
    font-weight: 500;
    font-size: 12.5px;
    color: var(--text-dim);
    padding: 7px 12px;
    border-bottom: 1px solid var(--border);
  }

  td {
    padding: 7px 12px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }

  tr:last-child td {
    border-bottom: none;
  }

  .num {
    text-align: right;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .nowrap {
    white-space: nowrap;
    color: var(--text-dim);
    font-size: 12.5px;
  }

  .parent {
    color: var(--text-dim);
    font-size: 11.5px;
    word-break: break-all;
  }

  .fname {
    word-break: break-all;
  }

  .pill.tiny {
    font-size: 11px;
    padding: 0 6px;
  }

  .center {
    text-align: center;
    padding: 28px;
  }

  .pager {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 14px;
    margin-top: 12px;
  }

  .linklike {
    border: none;
    background: none;
    padding: 0;
    color: inherit;
    text-decoration: underline;
    font-size: inherit;
  }
</style>
