<!-- 一行路径输入：文字框 + 浏览按钮。 -->
<script lang="ts">
  interface Props {
    label: string
    hint?: string
    value: string
    disabled?: boolean
    onbrowse: () => void
  }

  let { label, hint, value = $bindable(), disabled = false, onbrowse }: Props = $props()

  // label 的 for 要指向一个具体控件，多个实例同页时 id 不能撞
  const inputId = `dirfield-${Math.random().toString(36).slice(2, 9)}`
</script>

<div class="field">
  <div class="head">
    <label for={inputId}>{label}</label>
    {#if hint}<span class="muted hint">{hint}</span>{/if}
  </div>
  <div class="row">
    <input id={inputId} type="text" bind:value {disabled} placeholder="/Users/…" />
    <button onclick={onbrowse} {disabled}>浏览…</button>
  </div>
</div>

<style>
  .field {
    margin-bottom: 12px;
  }

  .head {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 5px;
  }

  label {
    font-weight: 500;
  }

  .hint {
    font-size: 12.5px;
  }

  .row {
    display: flex;
    gap: 8px;
  }

  .row input {
    flex: 1;
  }

  .row button {
    flex: none;
  }
</style>
