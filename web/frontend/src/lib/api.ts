// 后端接口的薄封装。所有请求走相对路径 /api，
// 开发时由 Vite 代理到 8787，生产时后端自己托管前端，两种模式写法一致。

import type {
  BrowseResult,
  Health,
  JobState,
  ModelsResult,
  Plan,
} from './types'

/** 后端返回的 detail 字段是给人看的中文说明，直接拿来当错误消息。 */
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  if (!resp.ok) {
    let message = `HTTP ${resp.status}`
    try {
      const body = await resp.json()
      if (body?.detail) message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* 响应不是 JSON，就用状态码兜底 */
    }
    throw new Error(message)
  }
  return resp.json() as Promise<T>
}

const post = <T>(url: string, body: unknown) =>
  request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

export const api = {
  health: () => request<Health>('/api/health'),

  browse: (path?: string) =>
    request<BrowseResult>(`/api/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`),

  models: () => request<ModelsResult>('/api/models'),

  saveModel: (payload: {
    exif_name: string
    folder_name?: string
    owner?: string
    color?: string
  }) => post<{ ok: boolean }>('/api/models', payload),

  /** 指定「读不出机型」那批文件的归属文件夹；传空串恢复默认。 */
  saveUnknownFolder: (folder_name: string) =>
    post<{ ok: boolean; folder_name: string; is_default: boolean }>('/api/models/unknown', {
      folder_name,
    }),

  startScan: (input_dir: string, output_dir: string, jobs = 8) =>
    post<{ job_id: string }>('/api/scans', { input_dir, output_dir, jobs }),

  startApply: (planId: string, dryRun = false) =>
    post<{ job_id: string }>(`/api/plans/${planId}/apply`, { dry_run: dryRun }),

  job: (jobId: string) => request<JobState>(`/api/jobs/${jobId}`),

  plan: (
    planId: string,
    opts: { offset?: number; limit?: number; device?: string; kind?: string } = {},
  ) => {
    const q = new URLSearchParams()
    if (opts.offset) q.set('offset', String(opts.offset))
    if (opts.limit) q.set('limit', String(opts.limit))
    if (opts.device) q.set('device', opts.device)
    if (opts.kind) q.set('kind', opts.kind)
    const qs = q.toString()
    return request<Plan>(`/api/plans/${planId}${qs ? `?${qs}` : ''}`)
  },
}

/**
 * 订阅任务事件。返回一个取消函数。
 *
 * 用 EventSource 而不是 fetch 流：断线它会自己重连，而服务端每次都从头补发，
 * 重连不会漏掉中间的事件。
 */
export function subscribeJob(
  jobId: string,
  handlers: {
    onLog?: (text: string) => void
    onProgress?: (done: number, total: number) => void
    onResult?: (fields: Record<string, string>) => void
    onDone?: (ok: boolean, state: string) => void
    onError?: (message: string) => void
  },
): () => void {
  const es = new EventSource(`/api/jobs/${jobId}/events`)

  es.addEventListener('log', (e) => {
    handlers.onLog?.(JSON.parse((e as MessageEvent).data).text)
  })

  es.addEventListener('progress', (e) => {
    const d = JSON.parse((e as MessageEvent).data)
    handlers.onProgress?.(d.done, d.total)
  })

  es.addEventListener('result', (e) => {
    handlers.onResult?.(JSON.parse((e as MessageEvent).data))
  })

  es.addEventListener('done', (e) => {
    const d = JSON.parse((e as MessageEvent).data)
    handlers.onDone?.(d.ok, d.state)
    es.close()
  })

  es.onerror = () => {
    // 任务结束时服务端会正常关流，EventSource 也会报 error——那不算故障。
    // 真正的连接问题由重连机制处理，这里只在彻底关闭时提示。
    if (es.readyState === EventSource.CLOSED) {
      handlers.onError?.('与后端的连接已断开')
    }
  }

  return () => es.close()
}
