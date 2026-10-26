# DecompCond 实际运行报告

配置哈希：`cb60d7aba74230ef2f01f0d4d97bcdf19bcca93e2576d89f765ca479656e3a00`

以下数量来自已保存的执行记录；未执行阶段不填入模拟指标。

## 6pyy_H7S_A_402 / single_b004_final_015

| 阶段 | 策略 | 尝试 | 结构合格 | J | 合格候选 ΔpIC50 均值 |
|---|---|---:|---:|---:|---:|
| baseline | baseline | 5 | 0 | 0.0000 | NA |
| round_001 | ring_first_staggered_oxygens | 2 | 2 | 0.8333 | -0.7642428874969482 |
| round_001 | oxygen_first_ring_late | 2 | 0 | 0.0000 | NA |
| round_002 | ring_first_c4_extended_reentry | 2 | 2 | 0.8889 | -0.7862632274627686 |
| round_002 | ring_first_c4_early_rotation | 2 | 2 | 0.8889 | -0.25436878204345703 |
| confirmation | ring_first_c4_early_rotation | 2 | 1 | 0.4444 | -0.8962388038635254 |
| confirmation | baseline | 2 | 0 | 0.0000 | NA |

已完成搜索轮数：2 / 2。

## 3w54_RNB_A_504 / single_b000_final_003

| 阶段 | 策略 | 尝试 | 结构合格 | J | 合格候选 ΔpIC50 均值 |
|---|---|---:|---:|---:|---:|
| baseline | baseline | 5 | 0 | 0.0000 | NA |
| round_001 | staged_anchors | 2 | 0 | 0.0000 | NA |
| round_001 | bond_seed_relax | 2 | 2 | 0.4167 | -2.2554409503936768 |
| round_002 | c2_continuous_anchor_tail | 2 | 0 | 0.0000 | NA |
| round_002 | anchor_precondition_late_c2_window | 2 | 0 | 0.0000 | NA |
| confirmation | bond_seed_relax | 2 | 0 | 0.0000 | NA |
| confirmation | baseline | 2 | 0 | 0.0000 | NA |

已完成搜索轮数：2 / 2。

坐标偏差在原蛋白坐标系计算。pIC50 是模型预测；独立 XYZ 重建采用原始模型元素预测，不能解释为仅凭坐标识别元素。
