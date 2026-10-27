# 评价加速与分层诊断 v2

本次变更由用户明确授权：优化后提交 GitHub，停止旧运行，从最后完整轮次继续。它是同一研究周期的评价政策升级，不是新的独立实验，也不增加生成预算。

## 保持与改变

ROOT 权重、生成器、积分器、教师、PLIP 条件选区、条件分组、种子、100 步时间表及排序规则保持不变。PoseBusters 全部验收项及阈值保持不变，包括内部能量检查。原子和键仍通过原始输出及独立 XYZ 重建评价。MMFF 应变、坐标偏移、子条件恢复及重入诊断继续计算。

`configs/evaluation-v2.yaml` 指定 4 个 CPU 评价进程，每个进程使用 8 个 CPU 的亲和性范围；ROOT 和亲和力预测只在主进程持有。工作进程通过 spawn 创建，PLIP 的全局设置和 RDKit 对象不跨进程共享。CPU 评价完成顺序可以变化，反馈仍按冻结路径、种子顺序汇总；整轮全部结果提交后才允许 Agent 规划。

体积重叠检查缓存同一受体的加载结果和按类别划分的原子子集；缓存键包含受体文件内容、加载参数和忽略类别。空类别直接返回原检查器相同的结果。每个候选仍重新计算空间邻域和 ShapeTverskyIndex，保留原搜索半径、尺度及碰撞阈值。原子删除使用 RDKit 批量编辑，不修改上游安装的 PoseBusters 源码。

仅对结构合格候选运行 pIC50；结构不合格时记录 `pic50=null`、`delta_pic50=null` 与明确的政策跳过原因。不能将跳过当成零分或技术失败。技术性缺失仍阻止下一轮。

## PLIP 抽样

基线与独立确认完整执行 PLIP。搜索轮次每条路径默认在 20 个发现种子中选 5 个。种子排序后分为 5 个连续层，每层用配置盐值、教师 ID、层编号和种子列表的 SHA-256 固定选择一个种子。选择与路径 ID、候选质量、是否结构合格无关，同一教师各路径和各轮复用相同选择，便于配对比较。少于 5 个种子时全选。

抽样名单写入每阶段 `evaluation_plan.json`，在评价前冻结。入选但化学无效的尝试保留“无法评价”状态，不替补抽样。未入选标记 `not_selected_by_policy`。反馈包含选中数、实际测量数、缺失原因、抽样规则和条件性解释；PLIP 均值只描述入选且化学可评价的样本，不能当作全部尝试的无条件恢复率。历史全量 PLIP 与新抽样值必须带政策标签比较。

## 运行与恢复伪代码

**Require:** 配置 K，冻结路径 Π，条件库 C，种子 Ξ，已生成或待生成尝试。

**Ensure:** 每个尝试有真实终态或明确技术错误；完整评价后才反馈。

```text
 1  P ← FreezeSeedStratifiedDiagnosticPlan(K, teacher, stage, Ξ)  ▷ 评价前选样，不读取候选质量
 2  outputs ← GenerateOrReuseEveryAttempt(Π, C, Ξ)              ▷ 已保存生成绝不重采样
 3  for output in outputs do
 4      VerifyRawHashAndDecode(output)                        ▷ 原始坐标不得被解码移动
 5      if chemical/generation invalid then CommitFailure()   ▷ 保留尝试和分母
 6      else SubmitToIsolatedCPUWorker(output, C, P)
 7  for cpu_report in completed worker jobs do
 8      VerifyXYZRecoveryAndAllPoseBustersChecks(cpu_report)   ▷ 包括内部能量与体积重叠
 9      SaveCPUCheckpoint(cpu_report)                         ▷ 亲和力重试不重复 CPU 评价
10      if structure_pass then score ← RootAffinity(output)   ▷ 只有主进程调用 ROOT
11      else score ← NOT_COMPUTED_BY_POLICY                   ▷ 数值为空，不填零
12      CommitEvaluationOnce(cpu_report, score)
13  RequireAllAttemptsHaveTerminalReports()                   ▷ 技术错误有限重试后暂停
14  SummarizeInFrozenPathAndSeedOrder(P, reports)
15  SendCompleteRoundFeedbackToAgent()
```

## 升级边界

`scripts/audit_evaluation_v2.py` 使用保存的原始 XYZ 复算所有已有可评价分子，逐项核对结构验收、恢复度、子条件指标和应变；不调用 ROOT 生成。pIC50 实现没有改动，PLIP 规则没有改动，仅其采样范围发生变化。

停止旧进程后，`scripts/fork_evaluation_upgrade.py` 核验旧源码、ROOT 源码与权重、配置、PoseBusters preset，以及覆盖全部停机时已有评价的审计。新目录保留完整轮次的原始汇总和评价；未完成轮次保留冻结路径与所有生成结果，旧部分评价隔离保存，并统一按新政策复评同一批原始输出。父目录保持不变，谱系写入 `continuation.json`。这允许从上一完整轮次继续，而不会丢失或重抽已生成的尝试。

新旧轮次的核心分数可比较，但 PLIP 反馈政策不同；当前周期不能冒称全部采用 v2 的独立对照实验。后续 auto research 应将此变更纳入谱系。

## 验证证据

- 远端依赖齐全环境：48 项测试通过；本地：39 项通过、3 项因未安装科学运行依赖而跳过。
- 原始输出回放：882 份正式评价，以及涵盖两位教师的 34 份接口评价，结构验收、恢复度、子条件、坐标偏移和 MMFF 应变逐项一致。正式迁移还要求审计覆盖停机时的全部已有评价。
- 4×8 核配置复核两位教师的 34 份接口评价，全部一致；该次回放用时约 218 秒。4×2 核对应回放约 494 秒；这是共享机器上的配置校准，不能直接作为正式全流程加速比。
- 8 个保存候选通过真实进程池、亲和力与抽样 PLIP 接口验证，再次执行命中 CPU 检查点；没有新增生成尝试。

审计必须重新调用 ROOT 原始输出解码器并保留双精度坐标及分子属性。曾用 SDF 重载的审计发现 18 个应变差异；SDF 的立体信息重建会影响补氢，不能作为严格等价比较的输入。原始解码回放消除了这些差异，失败的初始审计仍留在远端供追溯。聚合证据见 [验证记录](evaluation-v2-validation.json)。
