# 结构与恢复联合反馈、历史等暴露对照

本版本只修改路径 Agent 的反馈和指引。配置为 `configs/multi-style-agent-v2.yaml`；ROOT 的 MULTI 风格采样、条件、结构验收、有效恢复得分、亲和力次级排序以及全部预算沿用上一周期。

## 反馈语义

原 `exact_recovery_count` 是化学特征完全恢复的次数，其中可能包含结构不合格候选。新字段 `qualified_exact_recovery_count` 直接统计同时满足“结构合格”和“完全恢复”的交集；保留原字段以便对照。两个边际计数不能确定交集，旧摘要缺少此字段时保持缺失。

新增 `qualified_recovery` 保存结构合格候选恢复度的样本数、均值、标准差和范围。对存在合格候选的路径：

\[
\operatorname{有效恢复得分}
=\operatorname{结构合格比例}\times
\operatorname{合格候选平均恢复度}.
\]

没有结构合格候选时，有效恢复得分为零，条件均值未定义，记录 `null`。技术评价失败仍不能参与汇总或填零。上述分解用于解释得分变化，不增加指标权重，不改变既定排名。

该恒等式来自将全部尝试的有效恢复总和只在结构合格子集上求和，再乘除合格尝试次数；所有失败尝试继续保留在总尝试分母中。

`search_progress` 从发现阶段基线和所有已结束轮次计算逐轮最佳主分数、最近严格改善轮次及距离该轮的轮数。它按现有数值排序比较，不引入改善阈值或提前停止；主分数不变时，亲和力次级排序仍可能改善。

`matched_history_contrasts` 为上一轮每条路径寻找整个发现历史中每个模块激活时长均相等的其他路径，按活动集合差异最小、再按路径 ID 确定唯一描述性参考。参考包括真实逐步差异、空条件与重入变化、配对种子分数差和样本数。没有匹配时明确返回无匹配，不放宽激活时长要求。该参考不是强制父路径，也不是因果证明；匹配边际暴露不能同时控制所有共激活和重入因素。

## Algorithm 1 — 在原多轮搜索中构建新版规划输入

**Require:** 已冻结配置、该教师的发现历史、子条件库、下一轮编号。

**Ensure:** 不含本次独立确认结果的完整反馈；候选生成和评价流程保持原合同。

```text
 1  previous ← ReadEveryStrategyOfPreviousCompletedRound(history)  ▷ 首轮使用该教师的新协议静态基线
 2  VerifyAllAttemptsAndEvaluationBarrier(previous)                ▷ 有技术缺失时暂停，不能以零补齐
 3  feedback ← BuildExistingEnhancedFeedback(history, library)    ▷ 保留全部策略、子条件分母和配对比较
 4  for record in DiscoveryRecords(history) do                    ▷ 不读取独立确认或其他教师的历史
 5      if record contains measured joint and conditional counts then
 6          AddJointSuccessAndScoreDecomposition(feedback, record)▷ 交集来自实际候选，不能用边际计数推断
 7      else MarkLegacyFieldsUnavailable(feedback, record)        ▷ 旧记录缺少数据时不伪造成功或失败
 8  progress ← TracePrimaryBestFromBaselineAndCompletedRounds()  ▷ 主分数平台与亲和力次级改善分开描述
 9  for path in previous do
10      references ← OtherDiscoveryPathsWithEqualModuleDurations(path)
11      if references empty then MarkNoExposureMatch(path)
12      else ref ← NearestActivityScheduleWithStableTieBreak(references)
13           AddPairedScoresAndActualWindowDifferences(path, ref) ▷ 保留样本数、损失、空窗和重入差异
14  return Package(feedback, progress, frozen_sampling_contract)  ▷ 配置开启后传给同一真实 Agent 接口
```

**运行规范：** `agent.contrast_feedback` 由配置注入，启用时必须同时启用 `enhanced_feedback`。新指引 `skills/path-planning-contrast/SKILL.md` 按配置绑定的 SHA-256 加载；真实请求保存内容及哈希。Agent 仍一次提出全部完整路径，合法后整轮冻结。指引建议在主分数平台且路径集集中时尝试不同祖先、较早窗口或不同共激活机制，但不设置固定角色配额、原子 ID、路径模板或新阈值。

本次独立确认用新种子 5000–5019；此前 4000–4019 的结果已用于研究，成为研发数据。正式运行从新静态基线开始，两个教师各自维护历史，旧候选不作为新方法候选复用。

## 验证

远端完整测试 68 项通过；14 次真实 ROOT 技术尝试均完成评价，真实 Agent 的新反馈请求及合法路径已核验。另有两次基于历史发现数据的规划验证，各生成五条合法路径且不调用 ROOT。详情见 [验证指标](contrast-feedback-validation.json) 与 [Auto research 03](../research-cycles/cycle03.zh-CN.md)。这些小样本和接口测试不能代替完整实验。
