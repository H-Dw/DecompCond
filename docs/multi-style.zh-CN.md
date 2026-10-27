# FLOWR/MULTI 风格采样设置

用户要求已知原子的网络时间改为 0.999，并直接返回最后一次预测，尽可能对齐所核查的 FLOWR/MULTI 版本。新配置为 `configs/multi-style.yaml`；ROOT 权重保持冻结，不换成 MULTI 权重。

当前完整 Agent 流程使用 `configs/multi-style-agent-v2.yaml`，继承上述采样配置。新协议已开展真实生成：`multi-style-v1` 于 2026-10-27 21:50:55（北京时间）完成两位教师共 2120 次正式尝试；后继 `multi-style-agent-v2` 使用同一采样规则，只改进反馈与规划指引。实际进度以活动登记为准。“新协议尚未开展真实生成”仅描述提交 `63ee666` 当时的历史状态。

## 依据与配置

依据公开仓库提交 [`bbb8bfb4e8d8dd16bff1d56a7f96246920e643ba`](https://github.com/jule-c/flowr/blob/bbb8bfb4e8d8dd16bff1d56a7f96246920e643ba/flowr/models/fm_pocket.py)：已知网络时间 0.999（1014 行）、线性积分区间 0–0.999（2034 行）、初始自条件坐标缓存为零（2053 行）、返回最后预测而非覆盖后的积分状态（2195–2216 行）。上游也不额外固定无键或电荷（1018–1068 行）。这些描述仅针对已核查提交，不泛指所有上游版本。

| 参数 | 历史 ROOT 适配器配置 | 新 MULTI 风格配置 |
|---|---|---|
| `sampling.known_time` | 1.0 | 0.999 |
| `sampling.integration_end_time` | 1.0 | 0.999 |
| `sampling.readout` | `extra_prediction` | `last_prediction` |
| `sampling.initial_self_condition_coords` | `prior` | `zeros` |
| `sampling.final_epsilon` | 0.0001 | `null`，禁止追加前向 |
| 100 次更新、recycle=1 的前向次数 | 101 | 100 |

所有参数进入配置哈希和后端合同。候选还记录实际前向次数、读出类型及全局时间。这里的“最后预测”是最后一次更新之前的 clean 预测：100 步的步长为 0.00999，最后预测的全局时间为 0.98901，最后积分仍到 0.999。已激活原子的网络时间是 0.999，未激活原子使用全局时间；积分器只接收全局时间。固定区间仍以更新索引 `[0,100)` 描述，长度仍为 10 步的倍数。

## 保留的任务约束和实现差异

条件仍为 PLIP 所选原子的元素、参考 XYZ 与选区内部已有共价键，不扩展一跳，不添加固定电荷或无键。活动条件在预测前和更新后恢复，自条件缓存保存原始预测。返回的预测不贴回教师属性或坐标，继续用实际 XYZ 与化学预测进行评价。

生成器、先验、口袋编码、积分器及其随机流仍属于 ROOT。数值网格使用现有 Torch 实现，而上游所核查版本使用 NumPy；不保证逐位相等。动态路径允许条件切换，也是 DecompCond 的研究变量。因此这是采样约定对齐，不是 MULTI 模型复现或效果保证。初始缓存改为零是额外对齐项；进行因果分析时应单独消融，以免将多个设置的变化都归因于时间或读出。

## 运行与比较

```bash
python -m decompcond.cli validate --config configs/multi-style.yaml
```

新协议首次完整运行使用 `/workspace/DecompCond/runs/multi-style-v1`、确认种子 4000–4019，现已完成并保留原样。后继 `multi-style-agent-v2` 使用新的目录及确认种子 5000–5019，发现种子仍为 1000–1019。不得把新协议用于旧协议目录断点恢复，也不得将先前确认数据混入当前周期的规划历史。

建议先以相同教师、相同 PLIP 条件、相同种子比较静态基线，分别消融已知网络时间、读出方式、积分终点和初始缓存；随后再研究动态组合路径。任何改善结论必须来自真实分子评价，循环测试只验证时间、前向次数、读出和失败处理合同。

详细流程见主设计文档 Algorithm 5；它显式分开 `last_prediction` 与 `extra_prediction` 两个分支。

## 用于 Agent 条件生成的合同

新配置用于完整 `experiment`，并非只用于 `baseline`。静态基线、每轮全部 Agent 路径、独立确认都调用同一个 `RootBackend.generate_batch`，读取相同配置。Agent 不选择已知时间或读出方式；它只提出每个更新窗口的活动子条件集合。

每次规划输入新增 `sampling_contract`：包含配置派生的时间表、已知时间、初始缓存、读出规则、前向次数和每个允许窗口对应的预测时间。该合同带哈希，保存在实际 Agent 请求、后端合同和每次生成结果中；条件库哈希也随请求保存。指引 `skills/path-planning-multi/SKILL.md` 由新配置按内容哈希加载，旧周期的指引与哈希不变。

末段规划需要区分两个动作：`[90,100)` 的活动条件会在第 99 步预测前写入，所以能够影响返回的 clean 预测；该步积分之后的参考覆盖则不会再影响返回结果。末段为空时，模型仍保留此前生成状态和预测缓存，只停止覆盖条件；它不是重新采样，也不是完全遗忘条件。

| 末段安排 | 对返回输出的作用 |
|---|---|
| `[90,100)` 保持某子条件 | 参与最后 10 次预测，包括最终返回的预测 |
| `[90,100)` 为空 | 停止条件覆盖，保留此前状态和原始自条件缓存 |
| 第 90 步重入子条件 | 在该步预测前恢复元素、XYZ 与指定键，立即影响后续预测 |
| 最后一次积分后恢复参考 | 不再改变返回结果，没有额外末端预测 |

后续 Agent 优化默认继续冻结本采样合同；静态基线、全部动态路径及独立确认共同遵守。采样变更须另立方法对照，不能混入同一协议的路径优化。新版规划指引 `skills/path-planning-contrast/SKILL.md` 保留这些末段语义，并增加联合成功、主分数平台与历史等暴露对照，见 [反馈设计与伪代码](contrast-feedback.zh-CN.md)。

因此应让 Agent 对比“末段保持／重入条件”和“提前释放条件”的竞争假设，而不预设哪种必胜。保持条件可能有利于原子和键恢复，释放条件可能帮助周围结构调整，二者都需要以实际三维评价检验。比较末段时尽量匹配总激活时长，或明确说明暴露量同时改变。采样协议更换后，两位教师各自先获得新协议下的基线，再进行独立的十轮搜索；旧协议结果不当作本轮基线，确认结果不进入规划输入。

```text
 1  contract ← DescribeFrozenSampling(config)                       ▷ 固定时间、读出、缓存规则；Agent 无权修改
 2  baseline ← GenerateAndEvaluate(ConstantAllConditions,contract)   ▷ 使用新协议建立该教师自己的基线
 3  history ← Initialize(baseline)
 4  for round = 1, …, config.search.rounds do
 5      feedback ← SummarizeAllPreviousStrategies(history)           ▷ 等待上一轮全部评价完成
 6      paths ← Agent.Plan(conditions,contract,feedback,guidance)     ▷ 一次提出全部完整路径
 7      ValidateAndFreeze(paths,contract,conditions)
 8      results ← GenerateAndEvaluateAll(paths,contract)             ▷ 所有候选使用同一 ROOT 采样合同
 9      AppendRound(history,results)                                ▷ 保存失败，不用条件注入标签替代恢复评价
10  winner ← SelectAcrossAllRounds(history)
11  ConfirmWithFreshSeeds(winner,ConstantAllConditions,contract)     ▷ 确认不再回传给本次 Agent
```

## 已完成验证

2026-10-27 首版循环测试：本地 50 passed、4 skipped，远端真实 PyTorch/化学依赖环境 62 passed。测试执行适配器真实循环、替换模型为可核查的合成预测器，确认 100/101 次前向分支、最后预测时间、初始缓存、局部时间与全局积分时间隔离、积分器修改张量时原始读出不变，以及最后一步失败不被隐藏。这一阶段尚未加载 ROOT 权重，不能以该阶段测试推断分子效果。

同日提交 `63ee666` 完成首版 Agent 接入验证，远端全套测试 65 passed，检查配置派生时间合同、非默认步数和 recycle 次数、真实规划请求构造、技能文件哈希及生成记录的同一采样合同。这一阶段 API 响应使用测试替身；下列真实验证和正式运行发生在此提交之后。

随后完成真实接口验证：六组静态参数对照共 24 次 ROOT 尝试，真实 Agent 为两位教师各提出五条合法路径，每条另生成一个候选，共 10 次。34 次均有完整评价，详情见 [Auto research 02](../research-cycles/cycle02.zh-CN.md) 和 [验证指标](multi-style-validation.json)。技术种子与正式发现、确认种子分离；小样本结果只支持接口可运行和进一步检验，不能替代正式实验。

其后 `multi-style-v1` 完成 2120 次正式尝试并通过来源审计；分析见 [Auto research 03](../research-cycles/cycle03.zh-CN.md)。后继 Agent 版本通过远端 68 项测试、14 次真实技术尝试和两批历史发现数据规划检查后启动完整搜索，仍使用相同采样合同；技术验证另行记账。
