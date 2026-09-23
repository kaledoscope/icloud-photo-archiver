"""O-Data-Center-System — 功能模块包。

Shell 只做编排（调度、调用、日志、通知），所有判断逻辑住在 Python 里。

    modules/
    ├── utils/        共享基础设施：配置、清单、通知
    ├── preflight/    运行前环境检查
    ├── export/       从 Photos.app 导出 + 归档验证
    ├── cleanup/      本地中转清理 + iCloud 清理报告
    ├── backup/       远端同步（115 / NAS），每个目标一个独立模块
    └── scheduler/    流水线主干（launchd 调起）

调用方式（从项目根）::

    PYTHONPATH=<项目根> python3.11 -m modules.cleanup.clean_staging
"""
