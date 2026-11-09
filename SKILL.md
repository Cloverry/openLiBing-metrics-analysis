---
name: openlibing-nightly-rca
description: openLiBing 工程运营看板的夜间指标取证与 AI 根因分析。只有两个动作：先跑脚本（python run_pipeline.py，自动完成浏览器自举、取数、取证、归档、出页），再让 AI 补根因（python tools/list_pending.py → 分析 → python tools/write_analysis.py）。当用户要求「根因分析」「分析不达标/红灯指标」「nightly 失败原因」「工程运营看板取数」「切换周期看数据」时调用。
---

# openLiBing 夜间指标取证 + 根因分析

**只做两件事，不规划流程。** 采集、筛选、归档、出页、按周期重算全部已经在脚本里；这里只负责把脚本跑起来，再把 AI 分析这一步跑起来。

## 步骤一：开始执行

```powershell
python run_pipeline.py
```

一条命令跑完全部采集与出页。终端只透传步骤头与 `!!` 错误行，完整输出在 `out/state/run_pipeline.log`（不要整读，需要时 `Get-Content out\state\run_pipeline.log -Tail 50`）。

跑完会打印「待 AI 根因 N 条」。N=0 说明当天没有要分析的东西，到此结束（退出码 42）。改口径只改 `config.json`，不要改这个 skill。

## 步骤二：AI 开始分析

```powershell
python tools/list_pending.py                                              # 脚本给待分析清单（只读）
# AI 按 agent/agent_task.md 逐条分析，写出结论文件 out/state/analysis_result.json
python tools/write_analysis.py --file out/state/analysis_result.json --dry-run
python tools/write_analysis.py --file out/state/analysis_result.json      # 脚本原子写回
python run_pipeline.py --only step9                                       # 脚本重生成页面
```

AI 只做「读证据 → 写根因结论」：不生成页面、不做数据筛选、不改归档。写回只允许改 `analysis` 字段、只接受 `pending` 记录，越界一律报错跳过。

分析口径（分类体系、置信度分档、措辞规则、取证路径）在 `agent/analysis_profile.json`。

## 需要改代码时才看

| 想改什么 | 去哪 |
|---|---|
| 某一步采集逻辑 | 对应 `steps/step_*.py` 头部 docstring |
| 页面生成 | `steps/step_9_pages.py` + `tpl/` |
| 接口、陷阱、归档结构、配置项 | `docs/采集实现参考.md` |
