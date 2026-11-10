# ok-hacking-update

本仓库不是手写的：它是 `TieLaa/ok-hacking` 某个 tag 的**运行时子集**，文件清单由源仓库的 `deploy.txt` 决定。

- 同步任务：本仓库的 `.github/workflows/sync.yml`
  - 手动：Actions → **Sync from source** → Run workflow（可填 tag，留空取最新 `v*`）
  - 自动：每天 03:17 UTC（北京时间 11:17）检查一次源仓库的最新 tag
- 安装器的更新源就是这里（`pyappify.yml` 的 `git_url`），所以只放运行必需的文件。
- **不要直接改这里的文件**，下一次同步会覆盖。

发布流程：源仓库推 `v*` tag → 触发本仓库同步（手动或次日定时）→ 安装器按同名 tag 拉增量。