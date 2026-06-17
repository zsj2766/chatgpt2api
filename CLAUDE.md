# 项目规则 (chatgpt2api fork)

## 上游同步（最高优先级）
- 本仓库是 fork，定期合并上游 `origin`（basketikun/chatgpt2api）。
- **合并上游前必读 `UPGRADE.md`；合并完成后必须更新 `UPGRADE.md` 的「更新日志」与相关清单。** 同步流程、保留清单、坑点全在 `UPGRADE.md`。
- **注册机代码必须整体保留本地版**：`services/register/*`、`services/register_service.py`、`api/register.py`、`web/src/app/register/`。原因：上游注册机会被代理 Cloudflare 拦截，本地不会。合并时即便 git 自动合并成功也要 `git checkout HEAD --` 强制本地版。

## 本地根治的上游缺陷（合并时保留本地修复，勿被上游覆盖）
- **日志无限增长**：`services/log_service.py` 的 `_maybe_trim` 按行数轮转（上游也是纯 append，未修）。
- **log_service.py 必须保留** `LOG_TYPE_REGISTER`（注册机依赖）。

## 关键决策记录
- **watcher 用上游简化版** `start_limited_account_watcher`（根治 CPU 100% 死循环），放弃本地增强版的 token 临近过期自动刷新 + 限流自动恢复。详见 `UPGRADE.md` §5。
