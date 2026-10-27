# DecompCond：联合 inpainting 与十轮反馈搜索

2026-10-27 评价执行与诊断政策升级见 [v2 评价及恢复伪代码](docs/evaluation-v2.zh-CN.md)。核心验收与排序不变，新增进程隔离评价、受体缓存、失败结构的亲和力跳过和搜索阶段 PLIP 预定抽样。

本文件为当前实现合同，替代早期“仅图条件、坐标自由”的设计。生成模型是冻结的 **FLOWR.ROOT**；FLOWR.MULTI 仅提供 inpainting 思路。Agent 只规划条件在时间上的组合，不修改模型、教师、PLIP 选区、子条件、评价规则或候选。

## 1. 问题、输入与边界

输入为教师分子、受体及 PLIP 选择出的配体原子。完整教师图只用于解析、划分和来源核验。条件库包含选中原子的元素与参考 XYZ，以及两个端点都被选中的既有共价键。不扩展一跳，不以所有选中原子对构造“无键”条件，不固定电荷等未指定属性。其余区域由 ROOT 生成。

| 教师 target / node_id | 首轮重原子数 | 实际 PLIP 选中原子 | 选区内部键 | 连通子条件 |
|---|---:|---:|---:|---:|
| 6pyy_H7S_A_402 / single_b004_final_015 | 17 | 9 | 6 | 4 |
| 3w54_RNB_A_504 / single_b000_final_003 | 22 | 9 | 3 | 6 |

上述选区数量由新服务器重新执行 PLIP 得到。来源按归档成员名与 SDF `node_id` 精确匹配；文件记录序号仅作审计。原子稳定 ID 为原始重原子索引加一，和 SMILES 排列顺序无关。若输入已有冲突的 atom map，明确拒绝。

默认保持教师原子数；配置 `sampling.node_count_policy: explicit` 与 `node_count` 可在初始化前改变总数，并将必需原子映射到稳定槽位。不能少于所选原子数；一条轨迹内不动态增删槽位。增删未指定原子不改变条件库。

“soft”指条件集合可随时间变化。活动区段内采用精确 inpainting；并非浮点软标签、坐标松弛或几何候选拼接。条件关闭后停止覆盖，原状态继续演化；重入恢复同一个参考。能否优于全程固定选区是实验问题，不能从局部划分本身推出。

## 2. 配置、预算与运行规范

所有实验数值、路径、模型选项、API 设置、分组方式和评价阈值来自 YAML 配置 `K`，支持 `extends` 和 `--set key=value`。ROOT 依赖的其余原生默认值解析后完整记录到后端合同；不在 DecompCond 中隐式改动。

| 参数 | 默认值 |
|---|---:|
| 搜索轮数，含首次规划 | 10 |
| 每轮完整新路径数 | 5 |
| 每条路径尝试数 | 20 |
| 积分更新数 | 100 |
| 区段长度粒度 | 10 |
| 每位教师发现基线尝试数 | 20 |
| 确认时每条策略尝试数 | 20 |
| 基于表现提前停止 | 关闭 |

每位教师最大正式预算为 `20 + 10×5×20 + 2×20 = 1060`，两位共 2120。独立确认比较搜索优胜路径与静态基线。无合格搜索路径时返回失败，不为凑预算执行确认。技术验证单独记账。

- 所有窗口为更新索引的半开区间 `[start,end)`，完整覆盖 `[0,N)`。默认端点为 10 的倍数；允许空活动集合。
- 第 1 轮只读取基线；第 2–10 轮读取上一轮全部策略，以及历史摘要。先冻结整轮路径，再执行；轨迹内部不调用 Agent。
- 每轮复用同一发现种子组。确认种子与发现种子严格不交叠。随机状态按候选保存，避免批处理次序改变随机流。
- 完整路径展开为每步活动 ID 集合，以规范化序列哈希判重。历史重复提案返回已有摘要并要求有限修订；不占用新的生成尝试。改写区段表示不能绕过检查。
- 生成失败和化学无效是实验结果；技术故障为缺失。评价技术故障有限重试仍失败则暂停，不能填零或偷偷重采样。
- API 重试、JSON 修订和恢复不增加轮数。恢复只执行未持久化的候选或评价。批处理中断且尚未落盘的候选按原种子确定性重放，仍是同一尝试，不另取新种子。
- 配置、代码、后端、评价预设、条件库、路径、原始输出和评价来源均有内容哈希。运行目录不能混入另一套合同。修复运行算法后应新建技术验证目录。
- `proposal` 表示未提交状态；`CommitOnce` 只提交一次积分结果。不存在旧版本的临时几何候选组合或隐含 `Ω` 探测循环。`ξ` 表示明确记录的随机种子/随机流。

## 3. 条件表示与数学定义

令选区为 \(V_C\)，既有选区内部共价键为 \(E_C\)。每个原子事实为 \((i,Z_i^*,X_i^*)\)，元素和坐标绑定；每个键事实为 \((i,j,B_{ij}^*)\)。子条件 \(C_k\) 持有原子事实和键事实的 ID。

默认对诱导图 \(G[V_C]\) 取连通分量。BRICS 选项先在完整教师图找切分，再将每个片段投影到选区；切断处仍属于选区的键保存在显式 `C_interface` 中，避免漏掉条件。接口模块可只含键；不会自动激活端点的元素或 XYZ。组装使用 ID 并集去重。

路径给出 \(A_n\subseteq\{1,\ldots,K_C\}\)。原子掩码 \(m^X_n=m^Z_n\) 来自活动模块的原子事实并集；键掩码 \(m^B_n\) 仅包含活动模块明确拥有的键。所有非掩码位置保持原生状态。

对字段 \(Q\in\{X,Z,B\}\)，投影为

\[
P_{A_n}(Q)=(1-m^Q_n)\odot Q+m^Q_n\odot Q^*.
\]

XYZ 先按 ROOT 的受体中心与坐标尺度变换；最终输出逆变换回原蛋白坐标系。芳香键同时保存化学语义和由完整教师图产生的 Kekulé 编码：注入用后者，恢复评价用芳香感知后的语义，避免把单双键排列差异误当成芳香键丢失。

每步计算

\[
S_n^{in}=P_{A_n}(S_n),\quad
\widehat Y_n=F_\theta(S_n^{in},E,\tau_n,SC_n),
\]
\[
\widetilde S_{n+1}=\operatorname{NativeStep}(S_n^{in},\widehat Y_n,S_0,t_n,\Delta t_n;\xi),
\quad S_{n+1}=P_{A_n}(\widetilde S_{n+1}).
\]

已选原子的连续和离散网络时间为 `known_time`，其余为全局时间。**该局部时间仅进入网络编码**；积分器使用 ROOT 原生的全局时间与更新公式，不将局部时间放入 `1−t` 分母。电荷虽共享原生时间编码，但没有新增电荷投影。

自条件缓存由未覆盖预测产生；既不写入参考标签，也不把输出投影后的状态冒充模型预测。读出方式、全局积分终点和初始缓存坐标均由配置控制，并进入运行合同。

- 历史配置 `defaults.yaml`：已知时间 1.0，全局积分到 1.0，初始缓存坐标为先验；`readout: extra_prediction` 在终点减去 `final_epsilon` 后额外预测一次。默认共 100 次更新、101 次前向。
- FLOWR/MULTI 风格配置 `configs/multi-style.yaml`：已知时间 0.999，全局积分到 0.999，初始缓存坐标为零；`readout: last_prediction` 直接返回最后一次循环内的未覆盖预测，`final_epsilon: null`。默认共 100 次更新、100 次前向。生成权重、先验和积分器仍为 ROOT，不宣称复现 MULTI 模型。

新配置的全局步长为 `0.999 / 100 = 0.00999`；最后一次循环预测的全局时间为 `99 × 0.00999 = 0.98901`，随后仍执行最后一次积分到 0.999，但返回先前保存的 clean 预测。不能将最终积分状态或再次前向的结果冒充该预测。活动原子的网络时间始终为配置中的已知时间，未活动原子使用全局时间；局部时间不进入积分分母。所有路径区段按 0–99 的更新索引解释，10 步粒度不变。增加自条件 recycle 次数会增加前向数，不改变积分更新数。

两种读出均不在末端贴回参考标签或坐标。方法变更使用独立输出目录，不能混入正在执行的冻结轮次；详见 [MULTI 风格采样合同](docs/multi-style.zh-CN.md)。

## 4. 规范伪代码

以下 `▷` 为中文逻辑注释；`FAILURE` 为明确终态；`PAUSE` 表示保留现场后停止当前运行。每个有科学意义的参数均从 `K` 读取。代码模块名称列于每个算法后。

### Algorithm 1 — DecompCond 多轮反馈搜索

**Require:** 配置 \(K\)，教师集合 \(\mathcal D\)，冻结 ROOT \(F_\theta\)，路径 Agent，发现种子 \(\Xi_d\)，确认种子 \(\Xi_h\)。

**Ensure:** 完整尝试账本、各轮结果、跨轮优胜路径、独立确认或明确失败。

```text
 1  ValidateConfigAndBackend(K, Fθ); BindImmutableRunContract()       ▷ 核验种子、预算、权重及代码/评价哈希
 2  for teacher ∈ D do                                            ▷ 每位教师独立搜索，互不借用结果
 3      C, E, identity, frame ← PrepareConditions(teacher, K)        ▷ 算法 3：冻结条件、来源和坐标变换
 4      Πall ← ConstantPath(AllIDs(C), K.steps)                      ▷ 全程完整选区是唯一静态基线
 5      base ← ExecuteBatch({Πall}, Ξd[1:K.baseline], C, E, K)       ▷ 算法 4：生成、评价、持久化
 6      if base has technical error then CheckpointAndPause()       ▷ 不把环境故障解释为模型能力不足
 7      H ← InitializeHistory(base)
 8      for r = 1, …, K.rounds do                                  ▷ 10 轮包含第一次 Agent 规划
 9          feedback ← BuildCompleteFeedback(H, C, r, K)            ▷ 算法 2：上一轮全部策略 + 历史
10          Πr ← LoadFrozenPlanOrPropose(feedback, H, C, K)          ▷ 已冻结计划在恢复时不再次调用 Agent
11          if Πr invalid then CheckpointAndPause()                 ▷ 有限 JSON 修订后仍无效就停止
12          FreezeRound(r, Πr, Hash(H), Hash(C), Hash(K))             ▷ 本轮执行中禁止改路径和判定规则
13          reports ← ExecuteBatch(Πr, Ξd[1:K.candidates], C, E, K)   ▷ 先提交所有路径，再按设备容量分批
14          if reports incomplete then CheckpointAndPause()         ▷ 全部尝试有结果才可解除轮次屏障
15          AppendRound(H, r, Πr, reports)                           ▷ 保存证据引用、设计依据与路径谱系
16      end for
17      Πbest ← SelectAcrossAllSearchRounds(H, K.ranking)            ▷ 按 J、合格 ΔpIC50 依次比较
18      if Πbest absent then RecordSearchFailure(H); continue       ▷ 无结构合格候选，明确返回失败
19      FreezeSelection(Πbest)                                     ▷ 独立确认开始前禁止再选路
20      holdout ← ExecuteBatch({Πbest,Πall}, Ξh, C, E, K)            ▷ 相同确认种子比较两条冻结路径
21      SaveFinalReport(H, holdout)                                 ▷ 确认结果不进入本次任何规划上下文
22  end for
23  return AggregateSavedReports()
```

实现：`engine.Experiment`、`feedback.best_record`、`report.render_report`。

### Algorithm 2 — 从完整反馈策划整轮路径

**Require:** 当前教师的发现历史 `history`、固定条件库 `conditions`、轮次 `round`、配置 `config`。

**Ensure:** 恰好配置数量的合法新路径，或有限修订后失败；无生成调用。

```text
 1  previous ← Baseline(history) if round=1 else AllStrategies(history,round−1)
 2  VerifyAttemptAccounting(previous)                              ▷ 成功、化学无效、生成失败覆盖计划尝试
 3  table ← SummarizeEveryStrategy(previous)                        ▷ 数量、分布、路径有效恢复得分、有效 ΔpIC50
 4  defects ← SummarizeConditionsAndFailures(previous,conditions)    ▷ 分模块原子/键、结构检查、应变、PLIP
 5  transitions ← SummarizeExposureAndReentry(previous)              ▷ 组合顺序与暴露时长分别统计
 6  historical ← CompressDiscoveryHistory(history)                  ▷ 仅本教师、本采样协议的发现历史
 7  feedback ← PackageWithEvidenceIDs(table,defects,transitions,historical,Baseline(history))
 8  contract ← DescribeFrozenSampling(config)                       ▷ 明确局部时间、全局网格、末段读出及初始缓存
 9  guidance ← LoadAndVerifyPlanningGuidance(config)
10  PersistPlanningInput(conditions,contract,feedback,guidance)       ▷ 保存实际输入及条件库、采样合同哈希
11  for repair = 0, …, config.search.proposal_repairs do
12      proposal ← Agent.PlanCompleteBatch(conditions,contract,feedback,guidance,config.search.paths_per_round)
13      errors ← CheckSchemaAndGrid(proposal,config.sampling)        ▷ 全覆盖、无重叠、按更新索引；允许空集合
14      errors ← errors ∪ CheckIDsReferencesAndParents(proposal,conditions,history)
15      errors ← errors ∪ CheckExpandedDuplicates(proposal,history)  ▷ 不允许 Agent 更改采样合同或重复执行等价路径
16      if errors empty then return CompileAndFreeze(proposal)
17      feedback ← AttachErrorsAndCachedResults(feedback,errors)     ▷ 修订不增加轮次，不重新采样
18  end for
19  return FAILURE
```

API 传输重试在 `Agent.PlanCompleteBatch` 内独立计数；HTTP 错误正文和凭据不写入日志。提案仅存行动 JSON 与 usage，不索要或记录隐藏推理过程。证据 ID 的存在性可自动核验，文字依据是否过度推断仍需审阅。

实现：`agent.DeepSeekPlanner`、`sampling_contract.planning_sampling_contract`、`paths.compile_path/validate_batch`、`feedback.build_feedback`。采样合同只读；静态基线、所有 Agent 路径与独立确认共用同一后端配置。

### Algorithm 3 — 精确来源与子条件编译

**Require:** 教师配置、归档、受体、PLIP 配置、分组方式。

**Ensure:** 不扩张选区的化学 + XYZ 条件库及来源清单。

```text
 1  VerifyArchiveHash(); ExtractExactConfiguredMembers()            ▷ 不扫描记录序号猜测目标
 2  M ← UniqueSDFRecordWithNodeID(teacher.node_id)                   ▷ 不是唯一匹配则失败
 3  VerifyHeavyAtomsMapsConformerAndStoredScore(M)                   ▷ 参考记录必须与配置身份一致
 4  Eeval ← PrepareReceptorHydrogensWithoutHeavyAtomMotion(E)       ▷ 受体准备与生成坐标系保持一致
 5  I ← RunPLIP(M,Eeval,K.plip)                                     ▷ 检查 PDB 往返图与原子映射
 6  Vc ← UnionOfLigandAtomsReportedByPLIP(I)                        ▷ 只使用真实报告中的原子
 7  Ec ← {(i,j,b) ∈ Bonds(M) : i∈Vc ∧ j∈Vc}                        ▷ 只取既有选区内部键，不生成无键条件
 8  if K.partition = selected_connectivity then
 9      groups ← ConnectedComponents(Vc,Ec)                        ▷ 孤立原子成为独立模块
10  else
11      groups ← ProjectBRICSPartition(M, Vc, Ec)                   ▷ 全图仅辅助分组，不成为条件
12      AddExplicitInterfaceBonds(groups, Unowned(Ec))              ▷ 保留片段边界已有键
13  end if
14  BindEachAtomFact(i, element_i, XYZ_i)                            ▷ 同一原子的元素和 XYZ 同开同关
15  VerifyExactOwnership(groups,Vc,Ec); HashAndPersist(C)            ▷ 覆盖完整、无重复、无额外一跳
16  return C, E, identity, frame
```

PLIP 的 PDB 往返可能改变芳香性/键级；实现将原始 SDF 图转移到 PDB 对象后核验，并保持所有重原子位置。这是序列化修复，不是几何优化，也不向生成输出补入教师化学信息。

实现：`conditions.prepare_teacher/compile_conditions`、`plip.plip_profile`。

### Algorithm 4 — 整批执行、评价与恢复屏障

**Require:** 冻结路径集合、候选种子、条件库、后端与评价器。

**Ensure:** 每个计划尝试恰好一个终态，或保留技术故障现场并暂停。

```text
 1  jobs ← CartesianProduct(paths,seeds)                            ▷ 尝试 ID 由教师/阶段/路径/种子确定
 2  VerifyStageIdentity(jobs,Hash(C),Hash(K))                        ▷ 防止恢复到不同合同
 3  pending ← jobs without durable generated record
 4  for microbatch ∈ InterleavePathsAndBatch(pending,K.device) do
 5      outputs ← JointInpainting(microbatch,C,E,K)                 ▷ 算法 5；不同候选有独立掩码
 6      PersistEachActualOutputAndHash(outputs)                     ▷ 失败也保存，不用新种子替换
 7  end for                                                        ▷ 先完成本轮全部生成
 8  for job ∈ jobs do
 9      if evaluation exists then VerifySourceBindingAndReuse(); continue
10      report ← EvaluateWithBoundedTechnicalRetries(job,C,E,K)     ▷ 重试只重新评价同一原始输出
11      if report has unresolved technical error then CheckpointAndPause()
12      PersistReportBoundToRawOutput(report)
13  end for                                                        ▷ 全部评价完成才继续
14  VerifyNoMissingAttempts(); SummarizeEveryPath()
15  PersistRoundCompleteMarker(); return summaries
```

实现：`engine.Experiment.stage`。锁阻止同一目录被两个进程同时写入；原子文件替换避免半写 JSON。批次生成提交前崩溃可能重算同一随机尝试，不能保证“硬件只计算一次”，但不会增加逻辑候选或换种子。

### Algorithm 5 — 单候选联合 inpainting

**Require:** 冻结路径 `path`、条件库 `conditions`、冻结 ROOT `model`、受体 `receptor`、随机流 `random_stream`、配置 `config`。

**Ensure:** 未覆盖的实际模型终态、轨迹诊断，或生成失败。

```text
 1  prior, slots ← NativeInitialize(conditions,receptor,random_stream,config)
 2  state ← Copy(prior); cache ← InitializeSelfCondition(prior,config) ▷ 初始坐标缓存显式选择零或先验
 3  timeline ← LinearGrid(0,config.integration_end_time,config.steps+1)
 4  for step = 0, …, config.steps−1 do                              ▷ 路径使用更新索引，默认 100 次
 5      active ← UniqueUnion(conditions[path.ActiveIDs(step)])       ▷ 允许空条件、撤下和重入
 6      payload ← EncodeReferencesWithExplicitMasks(active,slots)    ▷ 元素/XYZ 绑定，键使用独立掩码
 7      model_input ← InpaintCopy(state,payload)
 8      network_times ← BuildPerAtomTimes(timeline[step],payload,config.known_time)
 9      prediction,next_cache ← PredictRoot(model,model_input,receptor,network_times,cache)
10      if prediction or next_cache nonfinite then return GenerationFailure(step)
11      if step = config.steps−1 and config.readout = last_prediction then
12          output ← DetachedCopy(prediction)                       ▷ 在积分前保存，避免张量别名改变输出
13      proposal ← NativeStep(model_input,prediction,prior,timeline[step],timeline[step+1],random_stream)
14      if proposal nonfinite then return GenerationFailure(step)   ▷ 最后一次积分失败也不可隐藏
15      RecordActivationReleaseAndRestoration(state,model_input,proposal,active)
16      state ← InpaintCopy(proposal,payload); cache ← Detach(next_cache)
17  end for
18  if config.readout = extra_prediction then                       ▷ 仅历史读出分支追加一次预测
19      readout_time ← config.integration_end_time−config.final_epsilon
20      output ← PredictRootAtGlobalTime(model,state,receptor,readout_time,cache)
21  if output nonfinite then return GenerationFailure(config.steps)
22  return DecodeWithoutProjection(output,slots,frame), output, trace ▷ 两分支均禁止末端参考覆盖
```

实现：`backends.root.RootBackend.generate_batch` 与 `inpainting`。批内失败行记录为失败，只保留有限占位状态使其他候选完成，不将占位输出作为成功样本。ROOT 权重逐张量核验，全部关闭梯度。

### Algorithm 6 — 三维审计、指标与路径排序

**Require:** 原始预测与 XYZ、固定条件库、既定评价配置。

**Ensure:** 可追溯的科学结果；技术缺失绝不填为零。

```text
 1  if generation failed then return FailedAttemptWithZeroContribution()
 2  M ← NativeDecode(raw)                                          ▷ 不做价态修补、不贴回教师事实
 3  if M chemically invalid then return InvalidAttemptWithZeroContribution()
 4  VerifyXYZUnchangedByDecoder(M,raw)
 5  Gxyz ← ReconstructFromRawElementsAndXYZ(M)                      ▷ RDKit 邻接 + OpenBabel 独立键级感知
 6  RZ, RB, per_condition ← AuditSelectedFacts(M,Gxyz,C)             ▷ 模型预测和几何证据共同支持
 7  g ← ChemicalAndPoseBustersAcceptance(M,E,K)                      ▷ 坐标偏差与应变不新增淘汰阈值
 8  strain ← RelaxCopyAndCompareMMFFEnergy(M,K)                     ▷ 原始重原子位置保留；不收敛返回 NA
 9  ΔpIC50 ← ScoreWithSameNormalization(M) − ScoreTeacherSameWay()  ▷ 与存档旧分数分开报告
10  plip ← RecoverInteractions(RunPLIP(M,E),ReferencePLIP(C))        ▷ 互作恢复仅评价，不追加生成约束
11  xyz_report ← DeviationsInOriginalProteinFrame(M,C)              ▷ 不做 Kabsch 对齐、不新增坐标门槛
12  PersistAllChecksAndEvidence(); return report
```

实现：`evaluation.Evaluator`、`feedback.summarize/best_record`。

## 5. 恢复度和统计合同

XYZ 本身不能唯一确定元素种类。因此采用**未覆盖模型元素预测 + XYZ 化学一致性审计**，不宣称从坐标独立识别元素。

对选中原子 \(i\)，原子命中要求：原始元素预测等于教师元素，且该槽位在模型图、RDKit 距离邻接和 OpenBabel XYZ 邻接中的局部连接一致。对选中键，要求两个端点原子命中、模型键与独立重建键的化学语义都命中，且距离邻接支持该键。审计的两种重建不接收教师键或 inpainting 掩码；独立重建失败作为几何支持不足记录。

\[
R_Z=|V_C|^{-1}\sum_{i\in V_C}h_i^Z,\qquad
R_B=|E_C|^{-1}\sum_{e\in E_C}h_e^B,\qquad
R=w_ZR_Z+w_BR_B.
\]

默认两权重均为 1/2。没有选中键时 `RB=NA`、`R=RZ`，不人为制造键恢复成功。

\[
J(\Pi)=\frac1M\sum_{i=1}^{M}g_i R_i.
\]

所有计划尝试进入分母。生成失败、化学无效或结构不合格贡献零；技术评价错误尚未解决时该路径没有可发布的 `J`。路径先按 `J`，同分再按结构合格候选平均 `ΔpIC50`；相同两项时使用稳定记录顺序。候选也按恢复度、pIC50 次序分析，不能仅反馈最高分。

对于生成失败或无法解码的化学无效尝试，恢复字段按失败约定记零，并明确标记 `zero_by_failed_attempt_convention_not_measured`。它表示该尝试没有可验收的分子，不表示已经测得每个原始元素预测都不匹配。分模块不可测项保留为缺失计数。首次激活与释放不计作重入，重入必须是曾启用的模块关闭后再次开启。

pIC50 为 ROOT 预测值，不是实验活性。教师与候选都在不移动重原子的副本上统一芳香感知、规范原子排序和 Kekulize 后评分，避免直接拿不同化学编码下的旧分数相减。归档 pIC50 保留为来源证据。

结构合格使用当前固定 PoseBusters `dock` 预设、单连通片段与无自由基检查。应变报告为固定重原子仅优化 H 后的能量与自由局部优化能量之差，单位 kcal/mol；不是全局最低能差。参数缺失或未收敛为明确 NA。不得因后验结果调整验收阈值。

每轮提交完整策略摘要：状态数量、恢复分布、分模块命中/缺失计数、PoseB 分项失败、应变状态、pIC50、PLIP、原坐标系 RMSD/最大位移、相对基线/前轮最佳/历史最佳的变化、激活/空窗/撤下/重入统计。所有候选原始报告保留。

## 6. 实现文件与验证层级

| 模块 | 实现 |
|---|---|
| 配置与继承 | `src/decompcond/config.py` |
| 来源、PLIP、条件划分 | `conditions.py`, `plip.py` |
| 路径、证据、判重 | `paths.py` |
| 冻结 ROOT 与掩码 | `backends/root.py`, `inpainting.py` |
| 三维评价 | `evaluation.py` |
| DeepSeek 与凭据 | `agent.py`, `credentials.py` |
| 十轮、恢复、反馈 | `engine.py`, `feedback.py` |
| 保存报告与真实执行图 | `report.py` |

单元测试中的合成后端只验证状态机，不是分子效果证据。真实接口验证与正式 2120 预算分目录。实际验证状态见 `docs/validation.zh-CN.md`；没有执行的轮次不会在报告中伪造结果。

DeepSeek 默认 `deepseek-flash`，支持直接环境变量和交互式 shell 环境。密钥及 SSH 密码仅运行时注入。服务器连接参数在配置中，默认端口 11099。版本、设备、词表、完整原生参数及模型哈希随运行保存。
