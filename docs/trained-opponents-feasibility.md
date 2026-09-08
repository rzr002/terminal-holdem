# 引入经过训练的程序对手：可行性与接入方案

核查日期：2026-09-08。范围是当前 52 张牌、2–9 个存活座位、无限注、盲注 10/20、起始 100 BB 的终端游戏。

**建议采用 OpenSpiel 作为训练与评测基础，开发可与 Codex 同桌的训练牌手。** 本次已验证两个框架的九人环境，以及一个小型游戏上的真实自我对弈学习过程；没有找到并验证可直接装入本游戏的官方九人高手权重。当前游戏仍使用原来的 Codex / strategic 选项，下面的训练牌手是待实现方案。

## 三个项目分别能提供什么

| 项目 | 已确认的产物 | 对当前九人桌的结论 | 建议用途 |
| --- | --- | --- | --- |
| Pluribus 原版 | 六人无限注研究成果、论文、主要组件伪代码与实验数据；论文明确说明没有发布完整代码 | 没有可直接安装的官方原版程序；六人结果不能外推为九人能力 | 参考行动抽象、自我对弈和局部搜索的设计 |
| RLCard | Python 环境、CFR/DQN/NFSP 等训练工具；模型注册表有 Leduc CFR、限注德州规则模型等 | 实测 1.2.0 可启动并跑完九人无限注，但没有注册完整九人无限注的预训练高手 | 教学、快速实验、训练接口备选 |
| OpenSpiel | C++ 游戏环境、Python 接口、CFR/MCCFR、NFSP、Deep CFR 等实现 | `universal_poker` 源码允许最多十人；本机实测九人、52 张牌、四轮下注可运行；仍需训练策略 | 首选训练与评测基础 |

依据：[Pluribus 原论文](https://www.science.org/doi/10.1126/science.aay2400)、[CMU 实验介绍](https://www.cs.cmu.edu/news/2019/carnegie-mellon-and-facebook-ai-beats-professionals-six-player-poker)、[RLCard 模型注册表](https://github.com/datamllab/rlcard/blob/d7d0a957baf4cc7225a50522adb0164bf130a9d0/rlcard/models/__init__.py)、[OpenSpiel 游戏参数源码](https://github.com/google-deepmind/open_spiel/blob/48401890ee9857e611678302371378175a8e4c6b/open_spiel/games/universal_poker/universal_poker.cc)、[算法目录](https://openspiel.readthedocs.io/en/latest/algorithms.html)。仓库名称里有 Pluribus，并不代表来自原作者或具备原论文的强度。

额外核查了两条可能的捷径：

- [Meta ReBeL 官方仓库](https://github.com/facebookresearch/rebel)公开的实现和预训练检查点针对 Liar’s Dice，不能直接载入德州扑克。
- [conorarmstrong/noregrets](https://github.com/conorarmstrong/noregrets)是第三方 Rust 复现，声明支持 2–6 人，有训练与对局命令。作者同时公开了明显策略漏洞和对外部牌手的负收益记录。因此它可以列入六人实验候选，但本次没有运行、验证其权重，也不把它当作原版 Pluribus 或当前九人的即插即用高手。

## 这次实际运行了什么

使用临时虚拟环境，Python 3.13.3 / macOS ARM64；没有给游戏增加必需依赖。直接核查 [PyPI 发行文件](https://pypi.org/project/open-spiel/2.0.2/#files)，OpenSpiel 2.0.2 提供本机可用的 CPython 3.13 ARM64 wheel，不需要按旧安装文档的 x86-only 描述自行编译。

可复现脚本：[probe_poker_frameworks.py](../scripts/probe_poker_frameworks.py)。结果：[poker-framework-probe.json](../validation/poker-framework-probe.json)。

| 实验 | 实测结果 | 证明的范围 |
| --- | --- | --- |
| RLCard 1.2.0，2/6/9 人各 100 手随机合法行动 | 300 手全部结束，收益和为零 | 基础运行可行，不代表规则完全一致或牌力优秀 |
| OpenSpiel 2.0.2，2/6/9 人，各用抽象动作与完整下注金额跑 100 手 | 600 手全部结束，收益和为零 | 九人、52 张牌、四轮无限注配置可运行 |
| OpenSpiel 两人 Kuhn，外部采样 MCCFR，固定种子 31，5,000 次迭代 | 可利用度从 0.45833 降至 0.01426，训练与中途评测约 0.36 秒 | 小型不完全信息游戏中确实发生了学习；这些策略不能用于完整德州 |
| RLCard 默认输入检查：交换一张底牌与一张公共牌 | 54 维输入完全相同 | 默认向量丢失了私牌与公共牌的区别，需要改编码 |

随机行动经常迅速全押或弃牌，以上耗时不能换算成强策略训练速度。Kuhn 只有极小的游戏树，也不能据此估算九人训练时间。CFR 的两人零和收敛结论不提供九人纳什均衡保证。

复现命令，在项目根目录执行：

```bash
python3 -m venv /tmp/holdem-ai-research
/tmp/holdem-ai-research/bin/python -m pip install -r scripts/research-requirements.txt
/tmp/holdem-ai-research/bin/python scripts/probe_poker_frameworks.py
```

本次下载并验证的官方发行包 SHA-256：

```text
rlcard-1.2.0.tar.gz
d8e74211cecb44e9b1ee37c18a9014fa5c5549af5f4fc25cd643eb591e15520b
open_spiel-2.0.2-cp313-cp313-macosx_11_0_arm64.whl
57bd99505d28c1582c8acca5560222f157fdbed1fdf36b4a02423a4974bf035a
```

## 真正的接入难点

**模型输入必须完整且一致。** RLCard 默认 54 维编码把自己底牌与公共牌合并为 52 位牌集合，另外只有自己的投入和最大单人投入；它的 `raw_obs` 和 `action_record` 有额外信息，但默认训练向量没有使用。当前项目的 `Hand.observation()` 已有分开的底牌/公共牌、每个座位的筹码、投入、弃牌状态及行动历史，应该围绕这些公开字段建立统一编码。不能从训练器的完整状态直接给推理模型传入其他人的私牌。参见 [RLCard 编码源码](https://github.com/datamllab/rlcard/blob/d7d0a957baf4cc7225a50522adb0164bf130a9d0/rlcard/envs/nolimitholdem.py)。

**下注编号不能直接互换。** 实测 RLCard 1.2.0 的五个动作编号是弃牌 0、过牌/跟注 1、半池 2、满池 3、全押 4；OpenSpiel 的 `fchpa` 则是弃牌 0、过牌/跟注 1、满池 2、全押 3、半池 4。仓库 master 又将 RLCard 过牌与跟注拆开，不能混用 master 文档与旧检查点。当前游戏的 `Action('raise', amount)` 使用本轮累计总额，必须经语义转换和合法性检查，不能把整数 2 当成同一种下注。

**默认 OpenSpiel 配置并非完整德州。** 实验显式设置 13 个点数、4 个花色、2 张底牌、`0 3 1 1` 公共牌和 4 个下注轮。九人、2000 筹码时，`fchpa` 提供 5 个动作，`fullgame` 的动作空间为 2001，神经网络直接覆盖每一个下注金额会显著增加负担。第一版应采用有限下注菜单，记录人类使用菜单外金额的真实历史，不把其实际下注改成训练菜单里的近似金额。参见 [OpenSpiel 配置测试](https://github.com/google-deepmind/open_spiel/blob/48401890ee9857e611678302371378175a8e4c6b/open_spiel/games/universal_poker/universal_poker_test.cc)。

**人数和筹码会变化。** 当前游戏玩家会淘汰，九人会逐渐变成六人、三人、单挑，每人的有效筹码也会偏离 100 BB。仅在固定九人、等筹码中训练的权重不能无条件用于后续局面。需要支持可变存活座位和筹码分布，或者为每种适用配置提供对应模型，并明确检查权重的适用范围。

**不容易被全押吓走，需要在评测中单独检验。** 平衡自我对弈不必然学会最快利用某个玩家的反复全押行为。应加入总是全押、频繁超池下注、过度弃牌和跟注站等对照牌手；但不能让模型只会克制这一组固定对手。活跃桌的参与感也要另测，牌力提高不意味着翻牌前跟注越多越好。

## 建议的实现顺序

以下是工程方案，不是已经提供的新启动参数。

1. **先建立规则与编码的一致性检查。** 保留 `holdem/engine.py` 为终端游戏结算依据，建立训练观察、下注菜单及合法动作掩码。对 OpenSpiel 做同牌局回放比较，至少覆盖单挑行动顺序、任意下注金额、不足额全押是否重新开放加注、多个边池、奇数筹码和淘汰座位。烧牌造成的牌堆顺序差异需要显式映射，不能用同一个随机种子就宣称双方拿到了同一副牌。
2. **第一版训练用 NFSP 的轻量网络。** 原因是它按对局收集经验，较容易在有限预算下跑通训练、保存和推理；OpenSpiel 有 [PyTorch NFSP 实现](https://github.com/google-deepmind/open_spiel/blob/48401890ee9857e611678302371378175a8e4c6b/open_spiel/python/pytorch/nfsp.py)。先在小型游戏验证学习与模型重载，再训练当前规则对应的实验策略。完整九人也只称“实验训练牌手”。MCCFR 用于小规模正确性基准；[Deep CFR](https://github.com/google-deepmind/open_spiel/blob/48401890ee9857e611678302371378175a8e4c6b/open_spiel/python/pytorch/deep_cfr.py)作为后续同预算对照，不直接将完整九人游戏树交给表格 CFR 穷举。
3. **增加独立的模型牌手适配器。** 沿用当前 `decide(observation, personality, cancel)` 接口，加载固定检查点，输出合法 `Decision`。训练在离线脚本中完成，游戏时只做推理。检查点记录规则版本、观察与动作版本、支持人数/筹码范围、训练步数、种子及评测结果；缺失或不兼容时明确报错。
4. **允许按座位选择 Codex 或训练模型。** 目前 `Application` 只有一个全桌牌手提供者，需要新增座位路由，界面逐座位显示来源。这样可以安排少数训练牌手与其余 Codex 同桌；默认 Codex 的选择仍由用户控制。自然语言 skill 只指导 Codex，程序模型的不同风格来自经过评测的策略快照或训练设置，不能仅换名字就称八种已训练策略。
5. **通过独立对局后再提供“强对手”档位。** 与当前 `strategic`、旧 `local`、反复全押牌手及未参加训练的策略快照轮换座位对局，按独立牌堆统计 bb/100 和区间。同时报告翻牌参与人数、主动入池率、面对全押的行动分布、不同筹码深度表现、延迟和内存。验收依据是保留测试集上的收益与体验，不能只看训练损失下降或打赢随机策略。

## 工作量与算力判断

本次确认本机能运行这些框架和小型学习实验。**完整九人训练何时足够强，目前没有可据实承诺的小时数。** 建议先做一段限定时长的采样与训练性能测量，再按实际对局吞吐、内存增长和保留集表现确定预算。

适配器、模型加载与混合牌桌属于常规开发；规则对齐和可靠评测是主要工程工作；训练至稳定强于现有对手属于开放的实验工作。第一阶段的交付应是“可重复训练、能保存并接入的实验牌手”，而不是“复刻职业级 Pluribus”。只有在基线比较显示收益后，才值得增加训练算力或局部搜索复杂度。
