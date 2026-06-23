"use client";

import { Activity, AlertCircle, CheckCircle2, Clock, Pause, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { fetchRegisterSystemStatus, type RegisterSystemStatus } from "@/lib/api";

const THREAD_LABEL: Record<string, string> = {
  "account-watcher": "账号刷新监控",
  "proxy-pool-recheck": "代理池复测",
  "image-cleanup": "图片清理",
  "r2-backup-scheduler": "备份调度",
};

function formatInterval(seconds: number): string {
  if (seconds >= 3600) return `${Math.round(seconds / 3600)}h`;
  if (seconds >= 60) return `${Math.round(seconds / 60)}min`;
  return `${seconds}s`;
}

export function SystemStatusCard() {
  const [status, setStatus] = useState<RegisterSystemStatus | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const data = await fetchRegisterSystemStatus();
        if (active) setStatus(data);
      } catch {
        // 静默：状态卡片为辅助信息
      }
    };
    void load();
    const timer = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  if (!status) {
    return (
      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-4 text-sm text-stone-400">加载系统状态中…</CardContent>
      </Card>
    );
  }

  const { threads, accounts, register, automation } = status;

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="space-y-4 p-4">
        <div className="flex items-center gap-2">
          <Activity className="size-4 text-stone-600" />
          <span className="text-sm font-semibold text-stone-800">系统状态</span>
          <span className="text-xs text-stone-400">每 5s 刷新</span>
        </div>

        {/* 左右分区：后台线程 + 自动化配置 */}
        <div className="grid gap-4 md:grid-cols-2">
          {/* 左：后台监控线程 */}
          <div className="space-y-2">
            <div className="text-xs font-medium text-stone-600">后台监控线程</div>
            {threads.length > 0 ? (
              <div className="space-y-2">
                {threads.map((t) => (
                  <div key={t.name} className="flex items-center gap-2 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2">
                    {t.alive ? (
                      <CheckCircle2 className="size-4 shrink-0 text-emerald-500" />
                    ) : (
                      <AlertCircle className="size-4 shrink-0 text-rose-500" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-medium text-stone-700">{THREAD_LABEL[t.name] || t.name}</span>
                        <span className={`text-xs ${t.alive ? "text-emerald-600" : "text-rose-600"}`}>
                          {t.alive ? "运行中" : "未活跃"}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 text-xs text-stone-400">
                        <Clock className="size-3" />
                        <span>间隔 {formatInterval(t.interval_seconds)}</span>
                        {t.last_run_at ? <span className="truncate">· 最近 {t.last_run_at.slice(11)}</span> : null}
                      </div>
                      {t.message ? <div className="mt-0.5 truncate text-xs text-stone-500">{t.message}</div> : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-stone-400">暂无已注册的后台线程</div>
            )}
          </div>

          {/* 右：自动化配置 */}
          <div className="space-y-2">
            <div className="text-xs font-medium text-stone-600">自动化功能</div>
            <div className="space-y-2">
              <AutomationBadge label="移除异常账号" enabled={automation.auto_remove_invalid_accounts} />
              <AutomationBadge label="移除限流账号" enabled={automation.auto_remove_rate_limited_accounts} />
              <AutomationBadge label="刷新后重登录" enabled={automation.auto_relogin_after_refresh} />
              <AutomationBadge label={`图片自动清理 (${automation.image_retention_days}天)`} enabled={true} />
            </div>
          </div>
        </div>

        {/* 账号状态汇总 */}
        <div className="space-y-2">
          <div className="text-xs font-medium text-stone-600">账号状态</div>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
            <StatBox label="总数" value={accounts.total} color="text-stone-800" />
            <StatBox label="正常" value={accounts.normal} color="text-emerald-600" />
            <StatBox label="限流" value={accounts.limited} color="text-amber-600" />
            <StatBox label="异常" value={accounts.abnormal} color="text-rose-600" />
            <StatBox label="过期" value={accounts.expired} color="text-stone-500" />
            <StatBox label="禁用" value={accounts.disabled} color="text-stone-400" />
          </div>
          <div className="text-xs text-stone-500">正常账号剩余额度合计：<span className="font-semibold text-stone-700">{accounts.total_quota}</span></div>
        </div>

        {/* 注册任务状态 */}
        <div className="space-y-2">
          <div className="text-xs font-medium text-stone-600">注册任务</div>
          <div className="flex items-center gap-2 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2">
            {register.enabled ? <Play className="size-4 text-emerald-500" /> : <Pause className="size-4 text-stone-400" />}
            <span className="text-xs font-medium text-stone-700">{register.enabled ? "运行中" : "未启动"}</span>
            <span className="text-xs text-stone-400">模式：{register.mode || "-"}</span>
            <div className="ml-auto flex gap-3 text-xs">
              <span className="text-emerald-600">成功 {register.success}</span>
              <span className="text-rose-600">失败 {register.fail}</span>
              <span className="text-stone-600">进行 {register.running}</span>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function StatBox({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="rounded-lg border border-stone-200 bg-white p-2 text-center">
      <div className={`text-base font-semibold ${color}`}>{value}</div>
      <div className="text-xs text-stone-500">{label}</div>
    </div>
  );
}

function AutomationBadge({ label, enabled }: { label: string; enabled: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-stone-200 bg-white px-3 py-2">
      <div className={`size-2 shrink-0 rounded-full ${enabled ? "bg-emerald-500" : "bg-stone-300"}`} />
      <span className="text-xs text-stone-700">{label}</span>
      <span className={`ml-auto text-xs ${enabled ? "text-emerald-600" : "text-stone-400"}`}>
        {enabled ? "已启用" : "未启用"}
      </span>
    </div>
  );
}
