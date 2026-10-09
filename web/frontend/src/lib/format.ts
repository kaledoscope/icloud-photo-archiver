/** 1536 → "1.5 KB"。与后端 modules/utils/files.py 的 human_size 同一套进位。 */
export function humanSize(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '—'
  let size = n
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (size < 1024) return `${size.toFixed(1)} ${unit}`
    size /= 1024
  }
  return `${size.toFixed(1)} PB`
}

/** "2025-02-08T19:19:57+09:00" → "2025-02-08 19:19" */
export function shortTime(iso: string | null): string {
  if (!iso) return '—'
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/)
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}` : iso
}

/** 只取年月，用于确认分类落点。 */
export function yearMonth(iso: string | null): string {
  if (!iso) return '未知'
  const m = iso.match(/^(\d{4})-(\d{2})/)
  return m ? `${m[1]}/${m[2]}` : '未知'
}

/** 日期来源的中文说明——界面上要让人知道哪些时间是靠文件修改时间兜底的。 */
export function dateSourceLabel(src: string): string {
  switch (src) {
    case 'exif':
      return '拍摄时间'
    case 'mtime':
      return '文件时间'
    default:
      return '未知'
  }
}

export function kindLabel(kind: string): string {
  switch (kind) {
    case 'photo':
      return '照片'
    case 'video':
      return '视频'
    case 'sidecar':
      return '旁车'
    default:
      return kind
  }
}

/** 计划里相对路径的父目录，用于在表格里分组显示。 */
export function parentOf(rel: string): string {
  const i = rel.lastIndexOf('/')
  return i === -1 ? '' : rel.slice(0, i)
}

export function baseName(rel: string): string {
  const i = rel.lastIndexOf('/')
  return i === -1 ? rel : rel.slice(i + 1)
}
