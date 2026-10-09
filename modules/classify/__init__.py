"""
classify — 分类引擎

把「一个媒体文件」判定成「归档树里的目标路径」，不依赖 Photos 库。

与 modules/export 的分工：
    export/   从 Photos.app 取素材（依赖 osxphotos）
    classify/ 判定去哪（纯逻辑，任何来源的素材都能用）

因此 Photos.app 导出的照片和硬盘上散落的老素材走的是同一套分类规则。
"""
