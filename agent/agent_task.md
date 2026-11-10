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

**批量模式（一次交互出全部结论）**：常规夜间流程本来就是「清单里几条就一次分析几条」。重分析已 `analysis.status=done` 的旧数据时，脚本会拦下这些条目，必须加 `--force` 才允许覆盖：

```powershell
python tools/write_analysis.py --file out/state/rean_result.json --dry-run --force
python tools/write_analysis.py --file out/state/rean_result.json --force
```

不要把一次批量任务拆成多轮对话（每轮只回几条）——证据都已落在归档里，读完全部记录后一次性输出 `items` 数组，字段与单条模式完全一致。

## 四、输出格式（唯一交付物）

写一个 `out/state/analysis_result.json`。下面第一条示例取自真实案例（`ops-transformer` #165），日志文件名与行号按归档规则示意：

```json
{"items": [
  {"recordId": "Ascend-CANN|ops-transformer#165",
   "keyError": "HCCL function error: hcclCommInitRootInfoConfig(...), error code is 4（首次出现 03:25:16 · aclnnMatmulAlltoAll · 全日志 1590 处）",
   "rootCause": "冒烟 job 由 4 个 pytest 分片串行执行 98 个用例，其中 19 个失败让 pytest 退出码变成 1，脚本以 exit 1 结束，冒烟阶段判定 FAILED，后续「归档」阶段停在 INIT（job_logs/02_ops-transformer_165_smoke.log:L902）\n\n第一类（15 个用例，MC2 多卡通信类算子）：最早在 03:25:16 的 aclnnMatmulAlltoAll 出现 HCCL function error: hcclCommInitRootInfoConfig(...), error code is 4，随后 DistBackend.forward_run 抛 ATKRuntimeError，被测试框架统一记成 aclnnXxx accuracy not passed——这是通信域初始化失败后的下游表现，并不是数值精度比对没过。同一签名从 03:25 持续复现到 04:13，全日志共 1590 处 HCCL function error（error code 4 有 1143 处、9 有 309 处、1 有 138 处、13 有 3 处），覆盖 MatmulAlltoAll / AllGatherMatmul / MatmulAllReduce / MoeDistributeDispatch 等全部 MC2 用例，因此更像多卡测试环境的 HCCL 通信域（ranktable / IP / 端口 / 驱动）问题，而非单个算子逻辑缺陷（job_logs/02_ops-transformer_165_smoke.log:L1383）\n\n第二类（4 个用例，MoeTokenUnpermuteWithEpGrad 精度与性能）：aclnnMoeTokenUnpermuteWithEpGradGetWorkspaceSize 返回 561002，tiling 内断言 permutedTokensGradOut shape must be (16384, 7680) 直接失败，说明该算子的 tiling 对 grad 输出形状有强约束，而这 4 个用例的数据不满足该约束，与通信无关\n\n已排除的干扰项：日志里的 No module named 'common'、AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'、ct-tool not installed 都属于环境/依赖告警，它们在第一个分片（最终 23 个用例全部通过）执行期间已大量出现，不是本次失败的决定性原因",
   "advice": "先从最可能的原因入手，排查多卡测试环境的 HCCL 通信域初始化：核对 2P/4P 环境的 ranktable、device IP 与端口（master_port 如 22118/25782）、HCCL 版本与驱动是否一致，并在已知正常的同型号节点上单独复跑 aclnnMatmulAlltoAll、aclnnAllGatherMatmul 各一次；若换节点后 error code 4/9/1/13 不再出现，即可判定问题在环境而不在算子\n\n若在健康环境上仍稳定复现，再查算子侧：比对 MC2 算子传入的 rank 数、通信域配置与用例自身的 2 卡/4 卡配置是否一致，排除通信域参数传错\n\nMoeTokenUnpermuteWithEpGrad 这一类需要单独修：对照 tiling 里 permutedTokensGradOut 期望 (16384, 7680) 的形状校验，核对该 4 个用例的实际 shape——用例数据变更过就更新用例，是算子回归就修 tiling 的形状约束逻辑\n\n验证方式：重跑 test_ops_transformer_part2/3/6/7 中上述 19 个用例，要求全部通过、日志中不再出现 HCCL function error 与 Failed to execute tiling function，并确认冒烟阶段退出码为 0、「归档」阶段能正常执行",
   "cites": ["job_logs/02_ops-transformer_165_smoke.log:L902", "job_logs/02_ops-transformer_165_smoke.log:L1383"]},
  {"recordId": "HPCKit|kuqcd#210",
   "rootCause": "构建第一步 git clone 内网用例库就鉴权失败，进程直接退出，所以后面没有任何编译输出——失败是「用例拉不下来」而不是「代码编不过」（job_logs/03_kuqcd_210_build.log:L138）\n\n已排除的干扰项：日志末尾的 timeout 提示出现在 clone 失败之后，是流水线中止的连带结果，不是独立故障",
   "advice": "先换凭据：更新 CI 侧的 git token 或部署公钥，并在同一节点上手工 clone 一次，确认权限已经放开、能正常取到分支\n\n验证方式：重跑该流水线，要求 clone 成功、构建能进入编译阶段且退出码为 0，日志中不再出现 Authentication failed",
   "cites": ["job_logs/03_kuqcd_210_build.log:L138"]}
]}
```

字段要求：

| 字段 | 要求 |
|---|---|
| `recordId` | 直接用清单里打印的原值（`组件\|流水线#构建号`）；`/` 写法也兼容 |
| `keyError` | **必填**，一句话报错原文：AI 从本次执行的日志与测试报告里筛出的「最有价值报错点」，即决定这次失败的那一行。页面的「错误信息」列直接展示它，所以要给真实报错原文（封装码还原成底层报错），不要带「首次出现 03:25:16 · 全日志 1590 处」这类统计修饰（统计写在 `rootCause` 里）；兜底的汇总行（如 `19 failed, 63 passed`、`exit code 1`）不算关键报错，除非日志里确实没有更具体的失败点 |
| `rootCause` | **必填**，散文式根因推断（写法见下节）：先说失败链，再按同一成因把失败归成几类并给出「更像…而非…」的推断，最后写已排除的干扰项。**只罗列「有哪些用例、报哪个错」= 不合格**，禁止 `一、二、三` 分点 |
| `advice` | 修复建议，按优先级递进的分段叙述（首选动作 → 条件触发的下一步 → 定点修复 → 验证方式），最后一段固定以 `验证方式：` 开头。禁止分点列举，禁止"建议排查""建议关注"这类空话 |
| `cites` | `文件名:行号` 数组，来自全量日志/用例日志/github 日志；`rootCause` 里的事实必须有至少一条引用 |
| `source` | 可省略，默认 `agent` |

脚本强制校验：`rootCause` 为空、`recordId` 找不到、记录不是 `pending`（且未加 `--force`）的条目会报错整条跳过，不会静默写入。重分析已 `done` 的旧数据时必须加 `--force`，否则每条都会被判为「只有 pending 才需要写回」而跳过。

### rootCause 的写法：先讲失败链，再给成因推断

页面把 `rootCause` / `advice` 按 `\n` 切成逐段渲染（空行 = 段间距，行首「…：」的短语会自动加粗成小标题）。
根因要回答的是「**为什么会这样**」，不是「**看到了哪些报错**」。固定结构：

第一段：失败链 —— 不列报错，只讲传导

```
冒烟 job 由 4 个 pytest 分片串行执行 98 个用例，19 个失败让退出码变成 1，脚本 exit 1，
冒烟阶段 FAILED，后续「归档」停在 INIT（job_logs/xx_smoke.log:L902）
```

第二段起：按**同一成因**归组，一组一段，段首写成「第 N 类（M 个用例，共性描述）：」，冒号后直接接着写，不要另起一行。段内必须包含四件事：

1. 最早出现的时间 + 首个报错原文（封装码要还原成真实报错）
2. 规模证据：同一签名出现次数、时间跨度、覆盖的用例/算子清单，带 `文件名:行号`
3. **成因推断**：「因此更像<原因 A>，而非<原因 B>」或「说明<机制>」——这是根因的核心，没有这句就不算根因
4. 必要时给反证：如果真是<原因 B>，应该还会看到<现象>，但日志里没有

最后一段：「已排除的干扰项：」+ 告警原文 + 为什么排除（例如它们在一份最终全部通过的分片里已大量出现）

```
【反例，不允许】
17 个 aclnn 用例 accuracy not passed，错误信息 HCCL function error error code is 4；
另有 4 个用例 tiling 失败（logs:123、logs:456）
——只有现象罗列，读者仍然不知道该改环境还是改代码

【正例】
…因此更像多卡测试环境的 HCCL 通信域（ranktable / IP / 端口 / 驱动）问题，而非单个算子逻辑缺陷
…说明该算子的 tiling 对 grad 输出形状有强约束，而这 4 个用例的数据不满足该约束，与通信无关
```

硬性要求：

1. 推断只能基于日志里出现的现象，不确定的写「更像 / 疑似」，不得当事实陈述
2. 不用 `一、二、三`、不写「分析过程：」「判定依据：」这类标签、不写 markdown（`**` `-` `1.` 表格）
3. 段内不换行，段与段之间只用一个 `\n\n`
4. 「已排除的干扰项」只在日志里确实出现易混淆告警时才写，没有就省略这一段
5. 记录里 `tasks`/日志确实缺失时，第一段就写清证据止于哪一层，不要用推断替代缺失的证据

### advice 的写法：优先级递进 + 验证方式

`advice` 是「拿去就能做」的修复路径，不是问题清单。按优先级递进分段，段间空一行：

1. **首选动作**：从根因里最可能的那一类入手，写清核对/修改什么、在哪核对、用什么结果判定（例如「换节点后报错消失 → 问题在环境」）
2. **条件触发**：以「若…仍复现，再…」开头，写排除首选原因之后的下一步
3. **定点修复**：某一类问题的具体修复位置（文件:行号 / 配置项 / tiling 校验点），写到能直接动手的粒度
4. **验证方式**：最后一段固定以 `验证方式：` 开头 —— 复跑哪些用例/任务、通过标准是什么、日志里不应再出现什么报错、阶段结果与退出码要求

硬性要求：

1. 不出现「一、」「二、」「1.」这类序号，靠分段和「若…再…」体现次序
2. 每个动作都要可执行、可判定，不写「建议排查」「建议关注」
3. 与 `rootCause` 的归组一一对应：根因里分了几类，建议里就要有对应的排查/修复路径
4. 「验证方式」不能只写「重跑验证」，必须有明确的通过标准

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
5. **证据止于「任务类型」这一层**：`tasks` 为空或 `counts.logTasks=0`（如 hcomm / shmem / MindIE-Motor 类）时，先看记录里的 `ftt`（服务端 `failedTaskType`，如「测试任务」「构建任务」）与 `status`，**服务端已经给出失败类型时不得判成"证据缺失"**：
   - `ftt` 非空：按类型定性 —— 含「测试」→ `test_failure`，含「构建/编译」→ `compile_failure`，含「部署/环境」→ `env_not_ready`，含「取消」→ `cancelled`；`rootCause` 的【结论】写「失败在<类型>阶段，平台侧未留存任务日志与失败用例明细，证据止于任务类型这一层」
   - `status=CANCELED`：定性 `cancelled`，【结论】写「运行被取消（CANCELED）」，并说明取消发生在哪个失败任务类型执行期间
   - 只有 `ftt` 为空、`status` 也不是 CANCELED 且无任何日志/用例时，才用 `evidence_missing`，结论写「该运行无任何任务日志与用例落盘，仅能从运行状态判定失败」
6. **排队类根因必须给时间线**：申请开始 → 首查状态 → 轮询次数与间隔 → 放弃/失败时间，逐点 `文件名:行号`；等待时长按起止时间差算，不得用"轮询次数×间隔"推算。禁止只写"资源调度失败"。
7. **指标异常类**：当日版本可用度非 1 且运行日志无失败时，检查是否"无成功运行 / 用例通过率未达 100% / 运行被取消"，按实际值定性。
8. **不得虚构**：日志没写的只能写"疑似/待核对"，不能当事实。

## 七、可变配置（改 `agent/analysis_profile.json` 即可，不改代码）

| 想改什么 | 改哪一节 |
|---|---|
| 定性分类体系（可增删类别） | `analysis.classification` |
| 置信度标准 | `analysis.confidence_rules` |
| 措辞硬性规则（含根因/建议写法） | `analysis.wording_rules` |
| 双维度指标定义与灯色 | `analysis.dimensions` |
| 失败任务类型（`ftt`）定性口径 | `analysis.deep_dive.failed_task_type_policy` |
| buildflow 子任务下钻规则 | `analysis.deep_dive.subtask_drilldown` |
| github 平台取证口径 | `analysis.deep_dive.github_evidence` |
| 排队类时间线举证要求 | `analysis.deep_dive.pending_timeline` |
| 封装错误码清单 | `analysis.wrapper_codes` |
| 附加专项关注点 | `analysis.extra_focus`（逐条执行） |

分析配置只影响 AI 的判断口径，不改动 `out/archive/` 下任何已归档数据，可对同一份证据反复重分析。
