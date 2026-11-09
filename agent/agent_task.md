# 第 8 步【agent 执行】AI 根因分析任务书

> 采集、归档、出页、按周期筛数据全部由脚本完成（`python run_pipeline.py`）。**AI 只做本文件描述的这一步**：
> 读脚本给出的清单与证据 → 写根因结论 JSON → 交给 `tools/write_analysis.py` 写回。
> 页面由第 9 步脚本生成，**AI 不写 HTML、不改数据、不抓接口**。

## 一、输入（全部只读）

| 来源 | 作用 | 怎么拿 |
|---|---|---|
| `out/archive/<年>/<年-月>/<日>/<组件>/<流水线>/records/run_<构建号>.json` | 单条执行记录（分析的最小单位） | `python tools/list_pending.py --show <recordId>` 会打印文件路径 |
| `job_logs/*.log` | 整 job 全量日志（同目录） | 记录内 `tasks[].logFile` / `errorLines` 行号 |
| `case_logs/*.html` | 失败用例日志（同目录） | 记录内 `tasks[].cases[].localLog` |
| `github_logs/gh_<runId>/` | GitHub 平台运行日志 | 记录内 `platform=github` 任务的 `logFile` |
| `agent/analysis_profile.json` | 可变量：定性分类、置信度、措辞规则、维度定义 | 直接读（小文件） |

## 二、分析范围（脚本已定，不要自行扩大）

- **只分析 `analysis.status=pending` 的记录**（`list_pending.py` 默认就只列这些）。
- `analysis.status=na` 的记录**不要分析**：它是成功运行，或当日两指标都为 1 的达标日。
- **vllm-ascend** 的口径已在采集阶段处理：不看板值是否为 1、按 `(scheduled)` 运行重算，只采定时运行。分析时按记录里的实际值走，不要另立规则。
- 清单里有几条就分析几条，不跳、不合并、不按流水线打包。

## 三、执行流程

```powershell
python tools/list_pending.py                      # 1) 清单：# / 组件 / 流水线 / 构建号 / 定性 / 置信度
python tools/list_pending.py --show "Ascend-CANN|ops-math#136"   # 2) 单条摘要（组件/时间/指标/计数/失败用例/记录文件）
# 3) 需要深挖时读记录文件与日志原文（按行号窗口读，见输出纪律）
# 4) 写结论到 out/state/analysis_result.json
python tools/write_analysis.py --file out/state/analysis_result.json --dry-run
python tools/write_analysis.py --file out/state/analysis_result.json
# 5) 重生成页面（不重采集）
python run_pipeline.py --only step9
```

## 四、输出格式（唯一交付物）

写一个 `out/state/analysis_result.json`：

```json
{"items": [
  {"recordId": "Ascend-CANN|ops-math#136",
   "rootCause": "18 个 aclnn 精度用例全部 accuracy not passed（aclnnTopK / aclnnBitwiseAndTensor 等），测试任务日志未落盘，只能用例断言消息",
   "advice": "在测试环境复跑这些 aclnn 用例，核对算子精度基线；并修复测试任务日志未上传问题",
   "cites": ["records/run_136.json:L120", "records/run_136.json:L186"]},
  {"recordId": "HPCKit|kuqcd#210",
   "rootCause": "git clone 内网用例库鉴权失败（fatal: Authentication failed），用例拉不下来直接退出",
   "advice": "更新 CI 侧 git 凭据 / 部署公钥", "cites": ["job_logs/03_kuqcd_210_build.log:L138"]}
]}
```

字段要求：

| 字段 | 要求 |
|---|---|
| `recordId` | 直接用清单里打印的原值（`组件\|流水线#构建号`）；`/` 写法也兼容 |
| `rootCause` | **必填**，一句话落到报错原文。禁止只写 `DEV-CODECI-35002` 这类封装码 |
| `advice` | 可执行建议，一两句，不写"建议排查""建议关注"这类空话 |
| `cites` | `文件名:行号` 数组，来自全量日志/用例日志/github 日志；`rootCause` 里的事实必须有至少一条引用 |
| `source` | 可省略，默认 `agent` |

脚本强制校验：`rootCause` 为空、`recordId` 找不到、记录不是 `pending` 的条目会报错整条跳过，不会静默写入。

## 五、输出纪律（硬性——防「输出过长，请输入继续」中断）

1. **禁止整读大文件**：`records/run_*.json`（含全量证据）、`job_logs/`、`case_logs/`、`github_logs/` 一律带 offset/limit 按行窗口读
2. **先摘要后原文**：先 `list_pending.py --show` 看摘要，再按 `errorLines` / `errorMessages[].line` 回原文读错误行前后 10~30 行
3. **单条命令回显 ≤100 行**：可能刷屏的命令加 `| Select-Object -Last 40` 或重定向到临时文件再读尾部
4. **不粘贴大段日志**：对话只回结论与 `文件名:行号`，原文写进 `cites`
5. **不向用户提问等待**：夜间无人值守，数据有缺口就按现有证据定性并在 `rootCause` 里注明"证据不完整"

## 六、取证与定性规则

1. **封装码不算结论**：`DEV-CODECI-35002` 等只说明"失败在云构建层"。先查整 job 全量日志里的真实报错（调度失败、环境拉起、资源不足、命令报错都能看到原文），只有确实无有效报错时才定性"平台/基础设施"并注明日志无有效报错。
2. **buildflow 任务（`subTasks` 非空）必须下钻失败子任务**：整 job 日志只有 `build_flow` 调度层记录（如 `Follow jobs are failed`），真实编译报错在 `subTasks[].errorLines` 与 `subTasks[].logFile`。
3. **test 任务**：以 `cases[]` 的 `description`（断言消息）+ `errorMessages`（带 `line`）为准，用例日志原文在 `localLog`；失败用例状态字段是 `state` 不是 `status`。
4. **github 平台任务（`platform=github`）**：`evidenceNote` 非空表示 GitHub 侧日志未留存（blob 404），按 `githubJob.conclusion` / 时长 / 失败步骤定性并注明证据不完整，**不得虚构日志内容**；测试 job 先看 `testResults`（`failedModels` / `aisbenchFailed` / `pytestFailed`），根因要定位到具体用例或模型。
5. **证据真缺**：`tasks` 为空、`counts.logTasks=0`、`evidence` 为空（如 hcomm / shmem 类）时，`rootCause` 写"该运行无任何任务日志与用例落盘，仅能从构建状态判定失败"，定性用 `evidence_missing`，别硬编根因。
6. **排队类根因必须给时间线**：申请开始 → 首查状态 → 轮询次数与间隔 → 放弃/失败时间，逐点 `文件名:行号`；等待时长按起止时间差算，不得用"轮询次数×间隔"推算。禁止只写"资源调度失败"。
7. **指标异常类**：当日版本可用度非 1 且运行日志无失败时，检查是否"无成功运行 / 用例通过率未达 100% / 运行被取消"，按实际值定性。
8. **不得虚构**：日志没写的只能写"疑似/待核对"，不能当事实。

## 七、可变配置（改 `agent/analysis_profile.json` 即可，不改代码）

| 想改什么 | 改哪一节 |
|---|---|
| 定性分类体系（可增删类别） | `analysis.classification` |
| 置信度标准 | `analysis.confidence_rules` |
| 措辞硬性规则 | `analysis.wording_rules` |
| 双维度指标定义与灯色 | `analysis.dimensions` |
| buildflow 子任务下钻规则 | `analysis.deep_dive.subtask_drilldown` |
| github 平台取证口径 | `analysis.deep_dive.github_evidence` |
| 排队类时间线举证要求 | `analysis.deep_dive.pending_timeline` |
| 封装错误码清单 | `analysis.wrapper_codes` |
| 附加专项关注点 | `analysis.extra_focus`（逐条执行） |

分析配置只影响 AI 的判断口径，不改动 `out/archive/` 下任何已归档数据，可对同一份证据反复重分析。
