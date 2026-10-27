# DecompCond

最新完整周期分析、MULTI 风格配对验证与下一周期设计：[Auto research 02](research-cycles/cycle02.zh-CN.md)。更早周期见 [Auto research 01](research-cycles/cycle01.zh-CN.md)。

冻结 FLOWR.ROOT，在推理的不同时间段启用、撤下、重入和组合 PLIP 子条件，并由 LLM 根据上一轮完整结果规划下一轮。

条件是 **所选原子的元素 + 参考 XYZ + 选区内部已有共价键**。默认按选区连通分量拆分，另有 BRICS 投影。模型预测前和积分更新后联合 inpainting；输出使用未覆盖的最终预测。FLOWR.MULTI 仅提供方法启发，不是本项目生成后端。

## 当前实现

新增 [`configs/multi-style.yaml`](configs/multi-style.yaml)：已知原子网络时间 0.999、全局积分终点 0.999、初始自条件坐标缓存为零，直接返回最后一次循环预测，不追加末端前向。100 次更新对应 100 次前向（recycle=1）。仍使用 ROOT 权重和积分器；[采样合同与中文伪代码说明](docs/multi-style.zh-CN.md)。历史配置保留原行为，新方法必须使用独立运行目录。

该配置覆盖完整 Agent 搜索，静态基线、动态路径和独立确认共用同一采样器。每次 Agent 请求包含带哈希的时间与读出合同；新指引解释末段激活、提前释放、条件暴露量对照及教师间历史隔离。Agent 只能修改组合路径。

评价加速配置：[`configs/evaluation-v2.yaml`](configs/evaluation-v2.yaml)。4 个独立 CPU 进程执行评价，保留全部 PoseBusters 检查；搜索阶段每路径预定抽样 5/20 进行 PLIP，基线与独立确认全量评价。恢复合同及中文伪代码见 [评价 v2](docs/evaluation-v2.zh-CN.md)。

- 配置驱动的来源核验、按 `node_id` 提取教师、真实 PLIP、稳定原子 ID 和条件划分。
- 冻结 ROOT 适配器、逐候选掩码、逐原子网络时间、原生积分器、独立候选随机流。
- 默认 **10 轮 × 5 条路径 × 20 次尝试**；每条轨迹 100 次更新，区段长度为 10 的倍数。
- DeepSeek 一次提交整轮路径；上一轮全部评价完成后才能再次规划。等价路径拒绝重复执行并返回已有结果。
- 独立 XYZ 成键审计、PoseBusters、MMFF 应变、统一 pIC50 重评分、PLIP 恢复、原坐标系偏移。
- 原子文件写入、每个候选的缓存、断点恢复、合同哈希、失败账本、真实执行 SVG。

详细算法、公式、中文注释和边界说明见 [设计与伪代码](partial-conditions.zh-CN.md)。实际验证和已知限制见 [验证记录](docs/validation.zh-CN.md)。代码测试中的合成后端只验证流程，不作为分子效果证据。

已完成两位教师共 **34 次真实 ROOT + DeepSeek 技术验证尝试**，以及首个 **2120 次正式尝试**周期。后续运行状态以活动登记为准；方法版本与结果不能混用。[查看初期接口验证流程图](docs/executed-flow.png)。

## 配置与预算

`configs/defaults.yaml` 是正式配置。实验参数均由配置注入，支持继承和命令行覆盖。两位教师分别维护历史；发现阶段复用种子，确认种子独立。

| 项目 | 每位教师 | 两位教师 |
|---|---:|---:|
| 固定完整选区基线 | 20 | 40 |
| 十轮搜索 | 1000 | 2000 |
| 优胜路径及基线的独立确认 | 40 | 80 |
| 最大正式尝试数 | **1060** | **2120** |

预算是尝试上限，不是必须凑满的有效样本数。生成失败和化学无效不替换；技术评价缺失暂停本轮。没有合格搜索路径则不执行确认。

`configs/smoke.yaml` 用于最小接口验证。`configs/interface.yaml` 用于两轮真实反馈验证，默认每位教师 5 个基线、每轮 2 条路径各 2 个候选、确认各 2 个候选，共最多 34 次技术验证尝试；与正式预算分目录。

## 安装

Python ≥ 3.11。先准备与目标 ROOT 权重兼容的 PyTorch、FLOWR.ROOT 源码及其依赖；DTK 设备需要匹配的厂商 PyTorch。权重与数据由用户提供，本仓库不下载或分发。

```bash
python -m venv --system-site-packages .venv
. .venv/bin/activate
python -m pip install -e '.[chem,test]'
```

真实评价另需 PLIP、OpenBabel Python 绑定和 PoseBusters。已验证的服务器依赖版本写入验证记录与运行清单；不要用通用 PyTorch 覆盖服务器的 DTK 构建。`configs/posebusters.yaml` 是随仓库固定的验收预设，来源见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 运行

从仓库根目录执行。先根据实际环境修改配置中的归档目录、ROOT 源码目录、权重、设备和输出目录。默认服务器连接端口为 11099；SSH 凭据仅运行时使用，不写入 YAML。

```bash
python -m decompcond.cli validate --config configs/defaults.yaml
python -m decompcond.launch doctor --config configs/interface.yaml
python -m decompcond.launch prepare --config configs/interface.yaml
python -m decompcond.launch baseline --config configs/interface.yaml
python -m decompcond.launch experiment --config configs/interface.yaml
python -m decompcond.cli report --config configs/interface.yaml
```

`launch` 在启动子进程前注入 `remote.environment`，供 DTK 动态库等使用。已有正确运行环境可直接用 `decompcond` 或 `python -m decompcond.cli`。`doctor` 只报告依赖、路径和凭据是否存在，不加载权重，不验证 API 网络可达性。

正式实验使用 `configs/defaults.yaml`，技术验证完成后再启动。同一配置重复执行 `experiment` 会恢复已保存任务；完整候选不重新生成，已经冻结的规划不重新请求 Agent。配置、算法代码、后端或评价预设有变化时应使用新输出目录。

```bash
python -m decompcond.launch experiment --config configs/defaults.yaml
# 示例：改变原子数政策需新建实验目录；固定的条件本身不会改变。
python -m decompcond.cli validate --config configs/defaults.yaml \
  --set sampling.node_count_policy=explicit --set sampling.node_count=24 \
  --set data.output_dir=/workspace/DecompCond/runs/size24
```

## Agent 与凭据

默认模型 `deepseek-flash`，端点、超时、重试、JSON 修订预算和提示词均在配置。支持两种密钥来源：

- `agent.credentials.mode: env`：读取当前进程的 `DEEPSEEK_API_KEY`。
- `agent.credentials.mode: interactive_shell`：启动配置的交互式 shell，读取其环境变量；不在终端询问密钥。POSIX 使用私有管道，shell 启动输出不进入日志。

不要把 API key、SSH 密码或含密码的代理地址放进配置、命令行或 Git。需要出网代理时，在进程环境提供 `HTTPS_PROXY`；HTTP 客户端继承该环境。API 失败停止并保存现场，不用手写路径冒充 Agent 结果。只保存行动 JSON、简短依据和 usage，不保存隐藏推理内容。

## 结果与指标

每位教师目录依次包含 `prepared/`、`baseline/`、`round_001/` …、`confirmation/`。候选目录保存 `generated.json`、`raw.npz`、`trace.json`、`evaluation.json`，可解码分子另存 `actual.sdf`。技术异常存 `evaluation_error.json`。原始结果留在运行目录，不进 Git。

运行根目录保存合同、状态、`report.zh-CN.md`、`executed-flow.svg` 和 `executed-schedules.svg`。这些图只展示已完成并评价的真实记录。

`candidates.json` 保留全部尝试，并分别给出发现阶段与独立确认的合格候选排序；每条记录关联原始评价文件的 SHA-256。

综合恢复度默认等于 `0.5 × 原子恢复率 + 0.5 × 键恢复率`；路径有效恢复得分等于结构合格候选的综合恢复度之和除以全部尝试数。失败保留在分母；技术缺失不填零。主分数相同时比较结构合格候选平均 ΔpIC50。独立确认不回传本次搜索。

元素来自未覆盖模型预测，结合 XYZ 邻接审计；**不能仅凭坐标唯一推断元素**。键级重建也可能歧义，因此报告保留预期、原始、重建键级和键长。pIC50 是模型预测，教师和候选采用相同芳香/Kekulé 编码规范后重评分，不直接减去编码可能不同的存档旧分数。选区 RMSD 只报告，不增加验收门槛。

## 测试

```bash
python -m pytest -q
```

测试覆盖十轮屏障、全部前轮反馈、1060 次预算计数、失败分母、API 重试、JSON 修订、历史最优、确认隔离、恢复不重复生成、路径等价判重、来源/选区不扩张、显式字段投影及局部时间不污染全局积分时间。没有 PyTorch 的开发环境会跳过张量测试；POSIX 凭据测试只在 POSIX 执行。

ROOT 接口当前要求 stacking self-conditioning，不支持额外生成特征通道、虚拟原子及 corrector 更新。对不兼容权重明确报错。新的上游版本需重新做接口验证，不能只改路径继续旧实验。
