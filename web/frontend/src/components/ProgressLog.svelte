<!--
  进度条 + 日志尾巴。扫描几千个文件要跑几分钟，得让人看见它确实在动，
  而不是对着一个转圈图标猜。
-->
<script lang="ts">
  interface Props {
    running: boolean
    done: number
    total: number
    logs: string[]
  }

  let { running, done, total, logs }: Props = $props()

  // total 未知时不要显示 0%，那会让人以为卡住了
  let pct = $derived(total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0)

  let box = $state<HTMLDivElement | null>(null)

  // 日志追加时自动滚到底
  $effect(() => {
    logs.length
    if (box) box.scrollTop = box.scrollHeight
  })
</script>

<div class="wrap">
  <div class="bar">
    <div class="fill" class:indeterminate={total === 0 && running} style="width:{total > 0 ? pct : 30}%"></div>
  </div>
  <div class="meta">
    {#if total > 0}
      <span>{done} / {total}　{pct}%</span>
    {:else if running}
      <span>正在统计文件…</span>
    {:else}
      <span class="muted">—</span>
    {/if}
  </div>

  <div class="log" bind:this={box}>
    {#each logs as line, i (i)}
      <div class="line">{line}</div>
    {/each}
  </div>
</div>

<style>
  .wrap {
    margin-top: 12px;
  }

  .bar {
    height: 6px;
    background: var(--surface-2);
    border-radius: 3px;
    overflow: hidden;
  }

  .fill {
    height: 100%;
    background: var(--accent);
    border-radius: 3px;
    transition: width 0.25s ease;
  }

  .fill.indeterminate {
    animation: slide 1.2s ease-in-out infinite alternate;
  }

  @keyframes slide {
    from {
      margin-left: 0;
    }
    to {
      margin-left: 60%;
    }
  }

  .meta {
    display: flex;
    justify-content: flex-end;
    font-size: 12.5px;
    color: var(--text-dim);
    padding: 5px 0;
    font-variant-numeric: tabular-nums;
  }

  .log {
    max-height: 240px;
    overflow-y: auto;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 10px 12px;
    font-family: var(--mono);
    font-size: 12px;
    line-height: 1.55;
  }

  .line {
    white-space: pre-wrap;
    word-break: break-all;
  }
</style>
