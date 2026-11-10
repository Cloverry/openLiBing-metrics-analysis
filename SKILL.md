---
name: openlibing-nightly-rca
description: openLiBing 工程运营看板的夜间指标取证与 AI 根因分析。一次调用跑完三件事：脚本取数归档出壳页（python run_pipeline.py）→ AI 补根因（python tools/list_pending.py → 分析 → python tools/write_analysis.py）→ 脚本重出页面（python run_pipeline.py --only step9）。当用户要求「根因分析」「分析不达标/红灯指标」「nightly 失败原因」「工程运营看板取数」「切换周期看数据」时调用。
---

# openLiBing 夜间指标取证 + 根因分析

**一处入口、一套流程、一次做完：取数 → AI 根因 → 出页。** 脚本负责取数、筛选、归档、出页；AI 只负责中间的根因结论。三段之间没有可选项，也没有「先跑到这里，剩下的下次再说」。

## 第 1 段 取数归档（脚本执行）

```powershell
python run_pipeline.py
```

一条命令跑完浏览器自举、取数、取证、归档，并生成页面静态壳。终端只透传步骤头与 `!!` 错误行，完整输出在 `out/state/run_pipeline.log`（不要整读，需要时 `Get-Content out\state\run_pipeline.log -Tail 50`）。

- 退出码 `0`：采集与归档完成，**必须接着做第 2 段**。
- 退出码 `42`：当天没有任何待分析数据，流程到此结束（这是唯一的提前结束条件）。
- 退出码非 `0`：按日志修好，用 `python run_pipeline.py --from stepN` 续跑，不得跳过。

## 第 2 段 AI 根因分析（agent 必须执行，脚本不做）

```powershell
python tools/list_pending.py                                              # 脚本给待分析清单（只读）
# AI 按 agent/agent_task.md 逐条分析，写出结论文件 out/state/analysis_result.json
python tools/write_analysis.py --file out/state/analysis_result.json --dry-run
python tools/write_analysis.py --file out/state/analysis_result.json      # 脚本原子写回
```

清单里有几条就分析几条，不跳、不合并、不按流水线打包。AI 只做「读证据 → 写根因结论」：不生成页面、不做数据筛选、不改归档。写回只允许改 `analysis` 字段、只接受 `pending` 记录，越界一律报错跳过。分析口径（分类体系、置信度分档、措辞规则、取证路径）在 `agent/analysis_profile.json`。写回成功后立刻做第 3 段，不要在这里收工。

## 第 3 段 重出页面（脚本执行）

```powershell
python run_pipeline.py --only step9
```

第 2 段写回的是归档里的 `analysis` 字段，页面数据是运行时读归档的，但页面的静态壳与索引仍需重生成一次，才算这份报告可交付。

## 完成判据（三条同时满足才算跑完）

1. `python tools/list_pending.py --strict` 退出码 `0`（还有待分析记录时它会以退出码 `2` 报「流程未完成」）
2. `python run_pipeline.py --only step9` 已执行
3. 归档与页面生成均无报错

**禁止**：只跑第 1 段就结束或汇报「已完成」；把「待 AI 根因 N 条」当成任务完成；用「下一步」的口吻把第 2 段留给人；AI 改 HTML、改归档、抓接口。

## 口径与约束（改代码前先看，别破坏）

1. **执行记录全量留存，成功只留存不分析**：第 1/2 步不看指标是否达标，目标产品下每条流水线的当日执行记录都进 `records/run_<构建号>.json`；成功运行 `analysis.status='na'`（只留存、不进 AI 队列），失败/取消且非 1 指标才 `pending`。这样按时间区间筛选时，分子分母与记录条数始终等于看板流水线数。
2. **`_day.json` 是当日口径的唯一真相**：流水线目录名、记录数、待分析清单、归档自检、结论写回都以当日 `_day.json.pipelines` 为准（`lib/paths.day_pipeline_scope`）。磁盘上多出的旧版本/改名前残留目录会被跳过并在输出里提示（`tools/list_pending.py`、`tools/verify_archive.py`），不要为了「看起来干净」手工删归档目录。
3. 流水线短名撞车（同产品下多条同名流水线）由 `steps/step_1_nightly.py` 的 `assign_short_names` 兜底去重，短名同时决定归档目录名与 `recordId` 前缀，改这里要连带回归第 2/7 步。

改口径只改 `config.json`，不要改这个 skill。

## 需要改代码时才看

| 想改什么 | 去哪 |
|---|---|
| 某一步采集逻辑 | 对应 `steps/step_*.py` 头部 docstring |
| 页面生成 | `steps/step_9_pages.py` + `tpl/` |
| 接口、陷阱、归档结构、配置项 | `docs/采集实现参考.md` |
