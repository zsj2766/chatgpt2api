# 上游同步维护指南

> 本仓库是 fork，长期跟随上游 `origin`（basketikun/chatgpt2api）更新。
> 除**注册机**与**上游缺陷的本地修复**外，其余尽量与上游保持一致。
> **每次执行上游合并后，必须更新本文档的「更新日志」与相关清单。**

---

## 1. 本地必须保留的部分（合并时绝不能被上游覆盖）

### 1.1 注册机（完整保留本地版，整文件 `git checkout HEAD --`）

本地注册机经实测**不会被代理 Cloudflare 拦截**（上游注册机会），核心价值，必须整体保留。

| 文件 | 本地关键内容 | 合并时要丢弃的上游改动 |
|---|---|---|
| `services/register/openai_register.py` | `requests.Session` + `is_socks_proxy`（规避代理 CF TLS 指纹拦截）；内联 `SentinelTokenGenerator` | Outlook 授权流程、CF 误判修复（3a3b871）、mail proxy 重构 |
| `services/register/mail_provider.py` | `requests.Session` 修复 | Outlook provider（+466 行） |
| `services/register/__init__.py` | `is_socks_proxy` | **上游已删除此文件内容**——本地必须保留，否则 openai_register/mail_provider 的 `from services.register import is_socks_proxy` 断裂 |
| `services/register_service.py` | `_inject_proxy_to_mail` | 上游 4296fdf 移除了 mail proxy handling |
| `api/register.py` | 本地端点 | 上游 outlook-pool 端点 |
| `web/src/app/register/`（page.tsx + components/） | 本地注册页 UI | 上游 Outlook UI |

> 即便 git 报告这些文件「自动合并成功」也要强制 `checkout HEAD`——自动合并会混入上游代码（实测 mail_provider 被混入 65 处 Outlook）。

### 1.2 三方合并时必须保留 HEAD 的本地修复

| 文件 | 本地修复 | 不能丢的原因 |
|---|---|---|
| `services/image_task_service.py` | 续轮询用**原账号 access_token**（`_token_for_email`） | 合并后 `OpenAIBackendAPI.__init__(access_token)`；上游用 `OpenAIBackendAPI(proxy_url=...)` 签名不兼容。不用原账号 token，续轮询读私有对话上游返回 404 |
| `services/log_service.py` | `LOG_TYPE_REGISTER` + `_maybe_trim` 日志轮转 | 注册机依赖 `LOG_TYPE_REGISTER`；轮转见 1.3 |

### 1.3 本地已根治的上游缺陷（合并时保留本地修复）

| 缺陷 | 位置 | 本地修复 | 上游状态 |
|---|---|---|---|
| 日志无限增长 + 读取 OOM（曾达 7.8GB） | `services/log_service.py` + 日志页/端点 | **日志走 stderr 不落盘**：`add()` 改 `logging` 输出 stderr（docker logs 采集），移除 `list/delete/_tail_trim` 等所有文件 I/O；移除 `api/system.py` 的 `/api/logs` 端点、前端日志页/导航/api 函数、`backup_service` 打包 logs.jsonl、`BackupInclude.logs`。保留 `LOG_TYPE_*` 常量与 `add` 入口（注册机依赖）。**合并时整文件保留本地版** | **上游也是纯 append，未修** |
| account-watcher 配置为 0 → 忙等待 CPU 100% | `api/support.py`（B 类取上游文件） | `interval_seconds = max(60, ...)` 下限保护。**合并 support.py 时务必保留此行**，否则 `wait(0)` 立即返回 → CPU 100% | 上游无下限保护 |

---

## 2. 快速更新上游操作流程

```bash
# 0. 拉取上游，看落后多少
git fetch origin
git log --oneline main..origin/main          # 上游领先的 commit

# 1. 新建工作分支（不要直接动 main）
git checkout -b merge-upstream-vX.Y.Z

# 2. merge 但不自动提交，便于逐文件处理
git merge origin/main --no-commit --no-ff

# 3. 【A 类】注册机强制本地版（无论是否冲突）
git checkout HEAD -- \
  api/register.py \
  services/register_service.py services/register/__init__.py \
  services/register/openai_register.py services/register/mail_provider.py \
  web/src/app/register/

# 4. 【B 类】取上游版（watcher / pow 等无本地自定义的）
git checkout origin/main -- api/app.py api/support.py utils/pow.py

# 5. 【C 类】手动编辑冲突块（git diff --name-only --diff-filter=U 看清单）
#    规则见 §3；解完 git add

# 6. 清理不想要的上游新功能（如 Outlook：grep -rin outlook 全仓库）

# 7. 验证（见 §4）全绿后提交
git commit                                   # 完成 merge
```

### 取上游版的关键命令区别
- 冲突文件（UU）：`git checkout --theirs/--ours -- <file>` 有效
- 自动合并成功文件（M）：`--theirs/--ours` **无效**，要取上游用 `git checkout origin/main -- <file>`，要恢复本地用 `git checkout HEAD -- <file>`

---

## 3. 三方合并规则（C 类）

- **保留本地**：本地有上游没有的修复（如 image_task_service 续轮询、log_service 的 LOG_TYPE_REGISTER）→ 手动保留 HEAD 那段，**不要** `checkout --ours`（会丢整个文件上游的非冲突改动）。
- **取上游**：上游修了 bug / 加了功能而本地没动 → 取上游那段。
- **合并双方**：两边都加了不同东西（如 INTERNAL_RESPONSE_KEYS）→ 拼一起。
- **判据**：看冲突块注释 + 上游 commit message（`git log -1 --format='%B' <hash>`）。

---

## 4. 验证清单（提交前必跑）

```bash
# 冲突标记残留
grep -rn "<<<<<<<\|>>>>>>>" services/ api/ web/src/        # 应为空

# 注册机保留本地特征（应非零）
grep -c "_inject_proxy_to_mail" services/register_service.py
grep -c "is_socks_proxy" services/register/openai_register.py

# Python 全模块导入
python -c "import services.register.openai_register, services.register.mail_provider, services.register_service, services.log_service, services.image_task_service; from api.support import start_limited_account_watcher; from services.log_service import LOG_TYPE_REGISTER; print('ok')"

# config.json 有效
python -c "import json; json.load(open('config.json',encoding='utf-8')); print('ok')"

# 前端构建（抓重复定义/类型错误，Python 检查抓不到）
cd web && node_modules/.bin/next build

# 不想要的上游功能已清理
grep -rin outlook web/src/ services/ api/                  # 应为空（除非决定要）
```

### 常见坑（真实经历）
1. **git 自动合并会把「双方都加的相同代码」叠加成重复定义** → build 报 `defined multiple times`（如 `setIsRelogining`、`resumeImagePoll`）。遇此 grep 同名声明去重。
2. **自动合并可能混入不想要的上游代码**（mail_provider 混入 Outlook）。注册机文件务必强制 `checkout HEAD` 后再 grep 复核。
3. **上游新增前端依赖**（package.json 更新）：`cd web && npm install --no-package-lock`。项目用 bun.lock，勿生成 package-lock 污染。
4. **Windows 工作区可能误产生 `nul` 文件**（保留设备名）→ `rm -f nul` 删除，否则 `git add -A` 失败。

---

## 5. 关键取舍记录（合并时做出的决策，便于回溯）

| 时间 | 决策 | 取舍 | 回溯方式 |
|---|---|---|---|
| 2026-06-17 | watcher 换上游简化版 `start_limited_account_watcher` | 放弃本地增强版的 token 临近过期自动刷新 + 限流自动恢复，换取根治 CPU 100% 死循环 + 简单稳定 | 本地增强版见历史 commit `9644b11` 的 `start_account_watcher`；`api/app.py` 改回 import 即可恢复 |

---

## 6. 更新日志（每次合并后实时追加）

### 2026-06-17 · 合并上游 v1.5.0（48 commit）
- **保留**：注册机全部本地版（A 类，§1.1）；image_task_service 续轮询 access_token 修复；log_service 的 LOG_TYPE_REGISTER + 新增日志轮转。
- **取上游**：`start_limited_account_watcher`（根治 CPU）、`utils/pow.py`（Sentinel PoW 最新）、config/account_service/image_task_service 非冲突部分。
- **清理**：上游 Outlook 邮箱源全部残留（后端 0 + 前端 6 处）+ outlook 测试脚本。
- **修复自动合并 bug**：`accounts/page.tsx` 重复 `setIsRelogining`、`api.ts` 重复 `resumeImagePoll`。
- **新依赖**：react-markdown、remark-gfm（上游 debug 页用）。
- **决策**：见 §5（watcher 简化版）。
- **遗留**：无未决；运行时（续轮询/chat 实链路）建议部署后实测。

### 2026-06-17 · 深度审核修复（合并后自检）
- **并发安全（重要）**：`log_service.py` 统一 `_lock` 串行化 `add`/`list`/`delete`/`_maybe_trim` 的文件操作。修复两个并发 bug：① `_write_count` 非原子自增竞态；② Windows 下 `_maybe_trim` 的 `replace` 与并发 `add.open("a")` 的文件锁冲突（`Permission denied`）。验证：20 线程×100 写并发 0 异常、文件无损坏。
- **死代码清理**：删 `register_service._bump` 中未使用的局部变量 `done`；去掉 `openai_register.py:650` 空 f-string 前缀。均为本地注册机**既有瑕疵**（非本次合并引入），清理不改功能、不影响 CF 抗拦截特性。
- pyflakes 全扫干净；前端 `next build` 通过。
- **CPU 兜底**：`api/support.py` watcher 的 `interval_seconds` 加 `max(60, ...)` 下限，防配置为 0 时 `stop_event.wait(0)` 立即返回导致忙等待。已排查全仓库后台线程循环（backup/image_cleanup/account_service 槽位/register 调度/sub2api 分页）均无裸忙等待。

### 2026-06-23 · config.json 合并遗留重复键清理
- `87bea78` 合并上游 v1.5.0 时 git 自动合并未识别 JSON 同键，遗留 4 组重复：`image_parallel_generation`(72/75 同值)、`image_settle_enabled`(76 `true`/99 `false`)、`image_check_before_hit_enabled`(77 `true`/100 `false`)、`image_settle_secs`(78 `2.0`/101 `2`)。
- JSON 后者覆盖前者，实际生效为上游保守默认（`false/false/2`），与本地 §1.3 图片调优意图相悖。
- 去重保留本地调优组（`true/true/2.0`，与 `config.py` 属性默认一致），删尾部孤立 3 键 + 重复 `image_parallel_generation`。**合并教训**：JSON 无重复键校验，三方合并后须人工 grep 同键。

### 2026-06-23 · 日志 OOM 根治 + 并发止血（Tier 0+1）
- **背景**：用户报「日志 OOM 仍存在」；核实发现 `3dc16f2` 的行数轮转治标不治本——`list()`/`delete()`/`_maybe_trim` 仍全量 `read_text().splitlines()`，且只有行数上限无体积上限，单行含 `request_text`(≤1000)+`urls` 可达数 KB，20 万行=数 GB。`AUDIT_REPORT.md` 声称的 stderr/空 list 方案从未落地（文档失实，勿信）。
- **日志根治（§1.3，整文件保留本地版）**：`log_service.py` 引入 `_MAX_BYTES=256MB` 体积主判据；`_tail_trim()` 字节级尾部截断（`st_size` 判体积→seek 尾部→回退 `\n`→仅读尾部写 tmp），不再全量读；`list()` 改尾部 seek 倒读、字节拼接后 decode（防多字节跨块丢字符）、凑 limit 即停，内存恒定；`delete()` 流式逐行重写；`bootstrap_trim()` 启动压缩历史超大文件（`api/app.py` lifespan 调用）；`_truncate_detail()` 单行 urls 截断（≤20 条 × ≤200 字符）。复现测试 `tests/test_log_service_oom.py` 8 用例全过。
- **生图并发止血**：`conversation.py` `stream_image_outputs_with_pool` `max_workers=min(n, image_max_total_concurrency)`（新增 config 项，默认 8），防 `request.n` 过大时线程/CPU 无界；`_generate_single_image` 加 `try/finally: backend.close()` 释放 curl_cffi session（`OpenAIBackendAPI` 新增 `close()/__enter__/__exit__`），防重试换账号时 TLS 上下文泄漏；`image_storage_service.save` 移除每次触发的 `cleanup_old_images()`（改由后台 30 分钟调度）。
- **注册机止血**：`register_service._normalize` `threads` 加硬上限 `min(32, max(1, ...))`，防用户设几千并发致线程/连接爆炸。
- **未做（Tier 2，待后续）**：PoW 纯 Python 自旋限并发、生图流式 yield 替代全收集（内存 N 倍峰值 I-2）、注册落盘节流、SSE 增量推送。本次范围：止血+日志根治。
- **保留**：`_maybe_trim()` 对外入口保留（转调 `_tail_trim`），不破坏文档/调用对应。

### 2026-06-23 · 日志方案最终定型 + 线程监控全链路接通
- **日志方案推翻重定**：上一条「字节尾部截断 + 倒读」方案虽修了 OOM，但仍落盘 logs.jsonl，文件持续增长未根治；且用户日常不用日志页。最终改为**走 stderr 不落盘**：`log_service.add()` 改 `logging` 输出 stderr（docker logs 采集），移除 `list/delete/_tail_trim/bootstrap_trim/_maybe_trim` 等所有文件 I/O；`_truncate_detail()` 单行 urls 截断保留。**整条日志链路移除**：`api/system.py` `/api/logs`+`/api/logs/delete` 端点、`LogDeleteRequest`；`api/app.py` `bootstrap_trim` 调用；`backup_service` 打包 logs.jsonl；前端 `web/src/app/logs/` 整目录、`top-nav.tsx` 日志导航、`api.ts` SystemLog/fetchSystemLogs/deleteSystemLogs/BackupInclude.logs、`backup-settings-card.tsx` logs checkbox、`settings/store.ts` logs 默认值、`config.py`/`config.json` BackupInclude.logs。**保留** `LOG_TYPE_*` 常量与 `add` 入口（注册机依赖，CLAUDE.md 要求）。这印证 `AUDIT_REPORT.md` §5.1 声称的 stderr 方案从未落地，本轮真正执行。
- **线程监控全链路接通**（用户报「看不到线程运行状态」）：`thread_status.py` 加 `_ThreadStatus` 单例（原模块只有函数无单例，导致 `from services.thread_status import thread_status` 失败）；`snapshot()` alive 阈值改 `max(interval*3,120)` 不 clamp 上限（原 clamp 300 致 30min 心跳的 image-cleanup 永远显示未活跃）。接入 3 个真实后台线程：`api/support.py` account-watcher、`image_service.py` _auto_cleanup_worker、`backup_service.py` r2-backup-scheduler（register+heartbeat）。`api/register.py` 加 `GET /api/register/system-status` 返回 `{threads, accounts, register}`；`account_service.get_stats` 加 expired 计数；`api.ts` 加 `RegisterSystemStatus`/`fetchRegisterSystemStatus`；`register/page.tsx` 挂载 `SystemStatusCard`。卡片 `THREAD_LABEL` 的 `proxy-pool-recheck` 后端无此线程（兜底显示 name，无害）。
- **自检**：后端 `py_compile` 全过 + `python tests/test_log_service_oom.py` 4 用例全过 + `create_app()` 冒烟（76 路由，logs 端点已删、system-status 已注册）+ 前端 `tsc --noEmit` 全过（含修预先存在的 `store.ts` canvas 类型推断瑕疵）。注意 `tsconfig.tsbuildinfo` 增量缓存会记旧路由，删 logs 目录后须删 `.next`+`*.tsbuildinfo` 再 tsc，否则报陈旧 logs 类型引用。
- **死代码清理**：`AUDIT_REPORT.md` 又一次列出完整待清理清单（§3.2）但未落地，本轮按其清单真正执行了日志页移除。该文档仍建议后续整体清理或标注存疑。
