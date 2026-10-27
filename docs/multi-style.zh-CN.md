# FLOWR/MULTI 风格采样设置

用户要求已知原子的网络时间改为 0.999，并直接返回最后一次预测，尽可能对齐所核查的 FLOWR/MULTI 版本。新配置为 `configs/multi-style.yaml`；ROOT 权重保持冻结，不换成 MULTI 权重。

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

先进行独立接口验证，再在新源码快照及新目录中开展正式实验。新配置预留输出目录 `/workspace/DecompCond/runs/multi-style-v1`，确认种子从 4000 起；启动时仍须检查该种子段未被其他研究使用。发现种子保持 1000–1019，便于与旧方法配对比较。当前 `research-cycle01` 的源码、配置与已产生候选不变，不允许将新配置用于旧目录断点恢复。

建议先以相同教师、相同 PLIP 条件、相同种子比较静态基线，分别消融已知网络时间、读出方式、积分终点和初始缓存；随后再研究动态组合路径。任何改善结论必须来自真实分子评价，循环测试只验证时间、前向次数、读出和失败处理合同。

详细流程见主设计文档 Algorithm 5；它显式分开 `last_prediction` 与 `extra_prediction` 两个分支。

## 用于 Agent 条件生成的合同

新配置用于完整 `experiment`，并非只用于 `baseline`。静态基线、每轮全部 Agent 路径、独立确认都调用同一个 `RootBackend.generate_batch`，读取相同配置。Agent 不选择已知时间或读出方式；它只提出每个更新窗口的活动子条件集合。

每次规划输入新增 `sampling_contract`：包含配置派生的时间表、已知时间、初始缓存、读出规则、前向次数和每个允许窗口对应的预测时间。该合同带哈希，保存在实际 Agent 请求、后端合同和每次生成结果中；条件库哈希也随请求保存。指引 `skills/path-planning-multi/SKILL.md` 由新配置按内容哈希加载，旧周期的指引与哈希不变。

末段规划需要区分两个动作：`[90,100)` 的活动条件会在第 99 步预测前写入，所以能够影响返回的 clean 预测；该步积分之后的参考覆盖则不会再影响返回结果。末段为空时，模型仍保留此前生成状态和预测缓存，只停止覆盖条件；它不是重新采样，也不是完全遗忘条件。

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

2026-10-27：本地测试 50 passed、4 skipped；远端独立源码目录的真实 PyTorch/化学依赖环境测试 62 passed。新增测试执行适配器真实循环、替换模型为可核查的合成预测器，确认 100/101 次前向分支、最后预测时间、初始缓存、局部时间与全局积分时间隔离、积分器修改张量时原始读出不变，以及最后一步失败不被隐藏。测试没有加载 ROOT 权重，也没有新增真实分子生成尝试；不能据此声称基线结构有效率已经提高。

同日完成 Agent 接入验证：远端全套测试 65 passed。新增检查覆盖配置派生时间合同、非默认步数和 recycle 次数、真实规划请求构造与新技能文件哈希，以及生成输出记录的同一采样合同哈希。API 响应使用测试替身，本次真实 Agent API 调用数和真实 ROOT 生成数均为零；实际规划效果仍需新协议下的完整实验。
