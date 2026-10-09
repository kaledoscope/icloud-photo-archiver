<!--
  照片归档分类器 —— 主界面

  流程刻意分成两步：先扫描出计划给人看，确认后才复制。
  不存在「点一下就开始搬文件」的路径 —— 源目录任何时候都只读。

  这个界面里没有、也不会有任何删除入口。
-->
<script lang="ts">
  import { api, subscribeJob } from './lib/api'
  import { humanSize } from './lib/format'
  import type { Health, PlanSummary } from './lib/types'

  import DirBrowser from './components/DirBrowser.svelte'
  import DirField from './components/DirField.svelte'
  import DeviceNaming from './components/DeviceNaming.svelte'
  import PlanReview from './components/PlanReview.svelte'
  import ProgressLog from './components/ProgressLog.svelte'

  // ===== 输入 =====
  let inputDir = $state('')
  let outputDir = $state('')
  let picker = $state<'input' | 'output' | null>(null)

  // ===== 扫描 =====
  let scanRunning = $state(false)
  let scanDone = $state(0)
  let scanTotal = $state(0)
  let scanLogs = $state<string[]>([])

  // ===== 计划 =====
  let planId = $state('')
  let summary = $state<PlanSummary | null>(null)
  let planInput = $state('')
  let planOutput = $state('')
  let planStale = $state(false) // 改过映射表，当前计划的文件夹名已经不是最新的了

  // ===== 复制 =====
  let applyRunning = $state(false)
  let applyDone = $state(0)
  let applyTotal = $state(0)
  let applyLogs = $state<string[]>([])
  let applyResult = $state<Record<string, string> | null>(null)
  let applyOk = $state<boolean | null>(null)

  // ===== 其他 =====
  let error = $state('')
  let naming = $state(false)
  let health = $state<Health | null>(null)
  let unknownLabel = $state('Unknown_Device')
  let unsub: (() => void) | null = null

  async function loadSettings() {
    try {
      unknownLabel = (await api.models()).unknown_label
    } catch {
      /* 读不到就沿用默认名，不该因此拦住整个界面 */
    }
  }

  $effect(() => {
    api.health().then((h) => (health = h)).catch(() => (health = null))
    loadSettings()
    return () => unsub?.()
  })

  let busy = $derived(scanRunning || applyRunning)
  let canScan = $derived(inputDir.trim() !== '' && outputDir.trim() !== '' && !busy)
  let planDevices = $derived(summary ? Object.keys(summary.by_device) : [])

  function fail(e: unknown) {
    error = e instanceof Error ? e.message : String(e)
  }

  async function startScan() {
    error = ''
    unsub?.()
    scanLogs = []
    scanDone = 0
    scanTotal = 0
    scanRunning = true
    planId = ''
    summary = null
    applyResult = null
    applyOk = null
    planStale = false

    try {
      const { job_id } = await api.startScan(inputDir.trim(), outputDir.trim())
      unsub = subscribeJob(job_id, {
        onLog: (t) => (scanLogs = [...scanLogs, t]),
        onProgress: (d, t) => {
          scanDone = d
          scanTotal = t
        },
        onDone: async (ok) => {
          scanRunning = false
          if (!ok) {
            error = '扫描未成功，详见下方日志'
            return
          }
          try {
            // 后端让 plan_id 与扫描任务同号，直接取
            const p = await api.plan(job_id, { limit: 1 })
            planId = job_id
            summary = p.summary
            planInput = p.input_dir ?? inputDir
            planOutput = p.output_dir ?? outputDir
          } catch (e) {
            fail(e)
          }
        },
      })
    } catch (e) {
      scanRunning = false
      fail(e)
    }
  }

  async function startApply(dryRun: boolean) {
    if (!planId) return
    error = ''
    unsub?.()
    applyLogs = []
    applyDone = 0
    applyTotal = 0
    applyResult = null
    applyOk = null
    applyRunning = true

    try {
      const { job_id } = await api.startApply(planId, dryRun)
      unsub = subscribeJob(job_id, {
        onLog: (t) => (applyLogs = [...applyLogs, t]),
        onProgress: (d, t) => {
          applyDone = d
          applyTotal = t
        },
        onResult: (r) => (applyResult = r as Record<string, string>),
        onDone: (ok) => {
          applyRunning = false
          applyOk = ok
        },
      })
    } catch (e) {
      applyRunning = false
      fail(e)
    }
  }
</script>

<header class="top">
  <div>
    <h1>照片归档分类器</h1>
    <p class="muted sub">
      按 <code>设备 / 年 / 月 / 来源 / 类型</code> 五级归类。
      <strong>源目录只读</strong>——只往你指定的输出目录复制，不移动、不改名、不删除。
    </p>
  </div>
  {#if health}
    <div class="health" title={health.project_root}>
      <span class="pill ok">服务正常</span>
    </div>
  {/if}
</header>

{#if error}
  <div class="banner error">{error}</div>
{/if}

{#if health && health.missing_tools.length > 0}
  <div class="banner error">
    <strong>外部工具缺失：{health.missing_tools.join('、')}</strong>
    {#if health.missing_tools.includes('ffprobe')}
      ，所有视频都读不出机型和拍摄时间，会被归到 Unknown_Device、年月用文件修改时间代替。
      常见原因是 Homebrew 装在 <code>/opt/homebrew/bin</code> 而服务环境的 PATH 里没有它。
      在 <code>config/config.env</code> 里写 <code>FFPROBE_BIN=/opt/homebrew/bin/ffprobe</code> 即可。
    {:else}
      ，图片元数据读不出来。
    {/if}
  </div>
{/if}

<section class="card">
  <div class="step-head">
    <span class="step-num">1</span>
    <h2>选择目录</h2>
  </div>

  <DirField
    label="源文件夹"
    hint="要整理的老素材，扫描过程不会改动它"
    bind:value={inputDir}
    disabled={busy}
    onbrowse={() => (picker = 'input')}
  />
  <DirField
    label="输出目录"
    hint="分类结果复制到这里，不能和源文件夹互相包含"
    bind:value={outputDir}
    disabled={busy}
    onbrowse={() => (picker = 'output')}
  />

  <div class="actions">
    <button class="primary" onclick={startScan} disabled={!canScan}>
      {scanRunning ? '扫描中…' : '开始扫描'}
    </button>
    <span class="muted">扫描只读元数据，不会复制任何文件</span>
  </div>

  {#if scanRunning || scanLogs.length > 0}
    <ProgressLog running={scanRunning} done={scanDone} total={scanTotal} logs={scanLogs} />
  {/if}
</section>

{#if summary}
  <section class="card">
    <div class="step-head">
      <span class="step-num">2</span>
      <h2>审阅计划</h2>
      <span class="spacer"></span>
      <button class="ghost" onclick={() => (naming = true)}>设备命名…</button>
    </div>

    {#if planStale}
      <div class="banner warn">
        映射表改过了，当前计划里的文件夹名还是旧的。重新扫描一次即可生效。
      </div>
    {/if}

    <PlanReview
      planId={planId}
      {summary}
      inputDir={planInput}
      outputDir={planOutput}
      {unknownLabel}
      onNameDevice={() => (naming = true)}
    />

    <div class="actions apply">
      <button class="primary" onclick={() => startApply(false)} disabled={applyRunning}>
        {applyRunning ? '复制中…' : `执行复制（${summary.files} 个文件，${humanSize(summary.total_bytes)}）`}
      </button>
      <button onclick={() => startApply(true)} disabled={applyRunning}>预演（不写文件）</button>
    </div>
  </section>
{/if}

{#if applyRunning || applyLogs.length > 0}
  <section class="card">
    <div class="step-head">
      <span class="step-num">3</span>
      <h2>复制</h2>
      {#if applyOk === true}
        <span class="pill ok">完成</span>
      {:else if applyOk === false}
        <span class="pill danger">有失败项</span>
      {:else if applyRunning}
        <span class="pill">进行中</span>
      {/if}
    </div>

    {#if applyResult}
      <div class="result">
        <div><span class="n">{applyResult.copied ?? '0'}</span><span class="l">已复制</span></div>
        <div><span class="n">{applyResult.skipped ?? '0'}</span><span class="l">跳过（目标已存在）</span></div>
        <div><span class="n">{applyResult.failed ?? '0'}</span><span class="l">失败</span></div>
        <div><span class="n">{humanSize(Number(applyResult.bytes ?? 0))}</span><span class="l">总大小</span></div>
      </div>
    {/if}

    <ProgressLog running={applyRunning} done={applyDone} total={applyTotal} logs={applyLogs} />

    {#if applyOk === true}
      <p class="muted after">源目录未做任何改动。可以打开输出目录看看结果。</p>
    {/if}
  </section>
{/if}

{#if picker}
  <DirBrowser
    title={picker === 'input' ? '选择源文件夹' : '选择输出目录'}
    initial={picker === 'input' ? inputDir : outputDir}
    onpick={(path) => {
      if (picker === 'input') inputDir = path
      else outputDir = path
      picker = null
    }}
    onclose={() => (picker = null)}
  />
{/if}

{#if naming}
  <DeviceNaming
    {planDevices}
    onclose={() => (naming = false)}
    onchanged={() => {
      // 映射表变了，计划里的文件夹名就旧了；顺带刷新兜底名显示
      planStale = true
      loadSettings()
    }}
  />
{/if}

<style>
  .top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 20px;
  }

  h1 {
    font-size: 22px;
  }

  .sub {
    margin: 4px 0 0;
    font-size: 13px;
  }

  .health {
    flex: none;
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-top: 14px;
  }

  .actions .muted {
    font-size: 12.5px;
  }

  .actions.apply {
    margin-top: 18px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
  }

  .spacer {
    flex: 1;
  }

  .result {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 12px;
    margin-bottom: 14px;
  }

  .result > div {
    background: var(--surface-2);
    border-radius: var(--radius);
    padding: 10px 14px;
  }

  .result .n {
    display: block;
    font-size: 20px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .result .l {
    font-size: 12px;
    color: var(--text-dim);
  }

  .after {
    margin: 10px 0 0;
    font-size: 12.5px;
  }
</style>
