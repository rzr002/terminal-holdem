# 对手策略与扑克 AI 调研

核查日期：2026-09-08。结论：扑克确实有很强的程序 AI；当前九人桌采用 **Codex + 角色 skill + 概率辅助计算**，另提供可直接运行的 `strategic` 程序牌手。它们尚未经过强化学习训练，也没有职业级或 GTO 水平的验证。

## 强扑克 AI 使用哪些方法

德州扑克属于不完全信息博弈：决策应针对对手可能的持牌范围，而不是假设知道其底牌。CFR（反事实遗憾最小化）通过自我对弈累积“当时换一个行动会怎样”的遗憾，逐步更新策略。两人零和博弈下的平均策略收敛结论，不能直接当作九人桌保证。参见 [CFR 原论文](https://papers.nips.cc/paper_files/paper/2007/hash/08d98638c6fcd194a4b1e6992063e944-Abstract.html)。

| 项目 | 公开证据与用途 | 与本游戏的匹配情况 |
| --- | --- | --- |
| [Pluribus](https://www.cs.cmu.edu/news/2019/carnegie-mellon-and-facebook-ai-beats-professionals-six-player-poker) | CMU 与 Facebook 的六人无限注研究系统，验证过对顶尖人类牌手的表现；使用自我对弈、混合策略与搜索 | 六人研究结论不是九人保证。本项目未获得或接入其模型权重 |
| [DeepStack 论文](https://arxiv.org/abs/1701.01724) / [公开示例](https://github.com/lifrordi/DeepStack-Leduc) | 两人无限注研究，结合持续重新求解与价值估计；公开示例针对简化 Leduc | 公开 Leduc 示例不是可直接接入完整九人德州的牌手 |
| [RLCard](https://github.com/datamllab/rlcard) | 卡牌强化学习环境，提供 DQN、NFSP、CFR 等算法；模型库的德州相关现成模型包括简化 Leduc CFR 与限注规则模型 | [无限注文档](https://rlcard.org/games.html#no-limit-texas-hold-em)展示两名玩家筹码编码与抽象动作，需要核对当前实现、重做九人编码和动作适配，并训练对应模型 |
| [OpenSpiel](https://github.com/google-deepmind/open_spiel) | 不完全信息游戏研究框架；[算法目录](https://openspiel.readthedocs.io/en/latest/algorithms.html)提供 CFR、外部采样 MCCFR 等实现 | 适合后续训练和评测工程，框架本身不是训练好的九人高手权重 |
| [fedden/poker_ai](https://github.com/fedden/poker_ai) | 开源 MCCFR 自我对弈、聚类和训练流程 | README 明确说明未修改时支持 20 张牌的牌堆；不能作为当前 52 张牌九人桌的即插即用模型 |

本次未找到并验证一个可以直接载入当前九人规则的现成高手检查点。没有下载未经验证的权重，没有启动长时间训练，也没有把简化牌局的训练结果当作完整德州能力。

## 已实现的增强

### 独立角色 skill

应用从项目 `skills/` 读取共享 `poker-core` 与当前座位的 `SKILL.md`，把全文放进该次 Codex 请求。模型不需要打开文件或调用工具，其他角色的私牌和决策不会进入请求。

| 角色 | Skill | 决策侧重 |
| --- | --- | --- |
| NOVA | [poker-nova](../skills/poker-nova/SKILL.md) | 活跃价值、小注宽入池、面对反复施压敢跟注 |
| BLAZE | [poker-blaze](../skills/poker-blaze/SKILL.md) | 位置施压、偷盲、3-bet 与半诈唬 |
| ECHO | [poker-echo](../skills/poker-echo/SKILL.md) | 混合线路、过牌范围保护、延迟下注 |
| MOSS | [poker-moss](../skills/poker-moss/SKILL.md) | 深筹码、隐含赔率与坚果潜力 |
| ATLAS | [poker-atlas](../skills/poker-atlas/SKILL.md) | 范围、赔率与下注尺度的一致性 |
| JADE | [poker-jade](../skills/poker-jade/SKILL.md) | 依据公开统计作有样本约束的调整 |
| RAVEN | [poker-raven](../skills/poker-raven/SKILL.md) | 阻断牌、河牌极化与诈唬组合 |
| ORBIT | [poker-orbit](../skills/poker-orbit/SKILL.md) | 有效大盲、短筹码与边池资格 |
| YOU 托管 | [poker-you](../skills/poker-you/SKILL.md) | 均衡决策；和对手一样独立竞争 |

共享底座采用用户选择的活跃休闲桌偏好：允许平跟和更多便宜看翻牌，仍区分价值下注、诈唬及全押防守。赔率与多人底池的实战背景参考 [PokerStars 赔率教程](https://www.pokerstars.com/poker/learn/lesson/pot-odds/)和[多人底池说明](https://www.pokerstars.com/poker/learn/strategies/a-guide-to-multiway-pots/)。具体角色偏好、程序阈值和范围权重是本项目设计的启发式，不是从求解器导出的图表。

### 概率与公开统计

- 每次决策默认做 192 次 Monte Carlo 抽样，按公开加注、跟注行为给予对手组合不同权重，平局按分池比例计入权益。
- 分层计算当前跟注后有资格争夺的主池与边池；不能将其他玩家的深筹码边池算作自己的回报。
- 输出位置、剩余有效筹码、SPR、听牌提示和合法下注总额候选。辅助计算只接收当前座位观察，不接触发牌器、未来牌或其他底牌。
- 在本次运行中累积公开 VPIP、PFR、面对下注的弃牌率和翻牌后激进因子。每次向模型提供样本数，skill 要求小样本时保持谨慎；退出后不保存。
- 单独记录主动全押手数，排除跟注至全押。至少观察三手、其中主动全押至少两手且比例不低于 40% 时，给对手当前全押范围加入更宽的组合权重，并提示模型扩大合理跟注。保留价值组合，不把频繁全押当作已知诈唬。
- 提供便宜看翻牌指引与全押者单挑权益；明确未行动者不一定会跟注，不能拿“全部人都摊牌”的低权益机械否定可玩起手牌。

范围采样有每位对手 12 次拒绝采样的上限，因此只是近似分布。胜率和跟注收益不模拟未来下注或精确弃牌率；范围权重、权益实现率折扣也未经训练。补牌和结束后亮出的其他底牌不会进入统计。

### 程序牌手

```bash
python3 play.py --agent strategic
```

该模式在本地执行起手牌范围、位置调整、行动加权抽样、赔率与下注规则，使用角色参数表达偏好，不执行自然语言 skill，也不调用 Codex。默认 `--agent codex` 保持不变，连接失败仍暂停，不自动切换。

旧的 `--agent local` 保留为比较基线：对随机对手牌抽样，加少量固定风格偏置。`strategic` 的设计更完整，但强度仍需要对局评测，不能从代码或测试数量推导胜率。

## 怎么验证强度

自动测试检验合法性、信息隔离、边池权益、免费行动、明显强弱牌和完整对局。它们不证明策略接近均衡。

```bash
# 纯本地评测：相同牌堆轮换候选牌手的座位，与旧 local 比较
python3 scripts/benchmark_agents.py --deals 20 --players 9 --samples 64 \
  --output validation/strategic-benchmark.json

# 三个虚构决策场景，真实调用 Codex，会使用账户额度
python3 scripts/check_strategy_codex.py
```

程序评测按独立牌堆汇总各座位轮换收益，再报告 bb/100 与近似区间，避免把同一牌堆的多次轮换当作独立样本。小样本、单一基线和同一组参数均限制结论；不能用短期赢筹码证明“专业级”。真实模型检查只确认明显场景与接入链路，没有衡量复杂牌局下的长期强度。

本次结果：

| 检查 | 结果 | 可得结论 |
| --- | --- | --- |
| [真实 Codex 场景检查](../validation/codex-strategy-smoke.json) | NOVA 前位 72o 弃牌；BLAZE AA 加注至 25；ORBIT 短筹码坚果跟注，三项通过 | 角色 skill 与计算结果接入真实模型，基本场景符合预期 |
| [首轮程序评测](../validation/strategic-benchmark.json) | 20 组牌堆 × 9 个座位，共 180 手；−91.50 bb/100，近似区间 [−326.55, 143.55] | 不确定性很大 |
| [独立牌堆复测](../validation/strategic-benchmark-holdout.json) | 参数未调整；200 组新牌堆 × 9 个座位，共 1,800 手；+58.28 bb/100，近似区间 [−29.74, 146.30] | 均值为正，但区间仍包含零，尚不能确认稳定强于旧 local |

两轮比较双方每次都使用 64 次抽样，游戏默认是 192 次。程序版结果不能替代 Codex skill 版本的强度评测，也不能外推到真实玩家或正式赛事。

### 活跃桌调整后的检查

上述强度比较是调整前版本的历史记录。用户随后选择更活跃、对反复全押有更多防守的桌风；这一目标不等于最大化对旧基线的收益。

使用相同 60 组牌堆、每次 48 次抽样，对九个程序座位仅跑翻牌前，主动入池比例从 15.19% 增至 41.11%；进入翻牌的手数从 25/60 增至 60/60，有翻牌时的平均人数从 2.16 增至 4.15。详见[调整前](../validation/table-activity-before.json)和[调整后](../validation/table-activity-after.json)。这是固定样本的活动度比较，不保证每手都有多人跟注，也不代表 Codex 的实测入池率。

`python3 scripts/check_table_style_codex.py` 使用真实 Codex 检查八位角色的便宜入池或反复全押防守场景，并额外检查垃圾牌仍能弃掉；运行会使用账户额度。

本次九项真实模型检查全部通过：NOVA、BLAZE、ECHO、MOSS 分别用 98s、KTo、66、A5s 跟小开池；ATLAS、JADE、RAVEN、ORBIT 分别用 AQs、JJ、KQs、99 跟反复全押者；NOVA 的 72o 仍弃牌。记录见 [Codex 活跃桌场景检查](../validation/codex-table-style-smoke.json)。这些是指定局面的单次响应，不代表长期跟注频率。另有 66 项本地自动测试通过。

## 若继续做强化学习版

建议先用 OpenSpiel 或 RLCard 的简化环境复现 CFR/NFSP，再建立与本引擎一致的训练接口。完整九人牌手需要固定人数和规则、设计位置/下注历史/多边池的观察编码、定义合法动作掩码及 raise-to 映射，用自我对弈训练并保存版本化权重。训练与推理必须只见各自信息集，评价器可见的隐藏牌不能流入策略。

通过单元测试后，还需要跨种子、大样本、不同基线及未知对手的轮换对局。只有适用的小型两人零和游戏，才把 exploitability 当作可计算的严格衡量；当前完整九人桌不伪报这一数值。以上是后续研发方案，本次没有宣称完成。
