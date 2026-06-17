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
| 日志无限增长（曾达 7.8GB） | `services/log_service.py` | `_maybe_trim()`：每 1000 次写入检查，超 `_MAX_LINES=20万` 行则原子裁剪到 `_TRIM_TO=15万` | **上游也是纯 append，未修** |
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
