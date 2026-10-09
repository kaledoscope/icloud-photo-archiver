// 与网页后端 /api 的应答结构一一对应。
// 后端那边改字段名时，这里会由 svelte-check 报出来。

export interface DeviceCounts {
  photos: number
  videos: number
  sidecars: number
}

export interface PlanSummary {
  files: number
  total_bytes: number
  by_device: Record<string, DeviceCounts>
  by_kind: Record<string, number>
  by_date_source: Record<string, number>
  skipped: number
  skipped_by_reason: Record<string, number>
}

export interface PlanEntry {
  src: string
  rel: string
  kind: 'photo' | 'video' | 'sidecar' | 'unknown'
  role: 'media' | 'sidecar'
  device: string
  model: string | null
  taken_at: string | null
  date_source: 'exif' | 'mtime' | 'unknown'
  size_bytes: number
}

export interface SkippedEntry {
  src: string
  reason: string
}

export interface Plan {
  plan_id: string
  created_at: string | null
  input_dir: string
  output_dir: string
  summary: PlanSummary
  skipped: SkippedEntry[]
  total: number
  offset: number
  limit: number
  entries: PlanEntry[]
}

export interface JobState {
  id: string
  kind: 'scan' | 'apply'
  state: 'running' | 'done' | 'failed'
  progress: { done: number; total: number }
  result: Record<string, string>
  error: string
  plan_path: string | null
  created_at: number
  finished_at: number | null
}

export interface BrowseResult {
  path: string
  parent: string | null
  subdirs: { name: string; path: string }[]
}

export interface ModelEntry {
  exif_name: string
  folder_name: string
  owner: string
  color: string
}

export interface ModelsResult {
  path: string
  entries: ModelEntry[]
  /** 读不出机型的文件当前归到哪个文件夹 */
  unknown_label: string
  /** 出厂默认值，用来判断用户有没有改过 */
  default_unknown_label: string
}

export interface Health {
  ok: boolean
  project_root: string
  python: string
  frontend_built: boolean
  /** 外部元数据工具是否就位，如 { sips: true, ffprobe: true } */
  tools: Record<string, boolean>
  /** 缺失的工具名。ffprobe 缺失会让所有视频静默降级成 Unknown_Device */
  missing_tools: string[]
}

/** SSE 事件。后端按 kind 命名，前端按 kind 分发。 */
export type JobEvent =
  | { kind: 'log'; text: string }
  | { kind: 'progress'; done: number; total: number }
  | { kind: 'result'; [key: string]: string | number }
  | { kind: 'done'; state: string; ok: boolean }
