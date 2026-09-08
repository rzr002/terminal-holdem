# Product Marketing Context

**Document version:** v4
**Last updated:** 2026-09-08

根据当前代码、测试和项目需求整理。受众与使用动机是定位假设，尚无用户调研或转化数据。

## Product Overview

**One-liner:** 在终端里，和 8 位 Codex 对手打德州扑克。
**What it does:** 九人桌无限注德州扑克，支持手动出牌、Codex 托管和全 AI 观战。每手结束公开所有底牌与公共牌；提前结束时另行展示带标记的复盘补牌。
**Category / type:** 本地 Python 终端游戏，虚拟筹码。
**Business model:** 项目没有付费功能、充值或提现；真实决策使用用户已有 Codex 账户额度。未制定商业计划或开源许可证。

## Target Audience

**Audience hypothesis:** 已安装 Codex CLI、喜欢终端工具和德州扑克的个人用户。
**Primary use case:** 在终端休息时玩一手，或观察模型如何出牌。
**Jobs to be done:** 与模型对战；临时托管自己的座位；亮牌后复盘自己的判断。
**B2B personas:** 不适用。

## Problems & Pain Points

想直接在终端玩牌，也想确认对手确实由 Codex 决策。主要使用门槛是 CLI 登录、网络连接与模型等待时间；这些必须在上手说明中讲清楚。没有调查证据支持量化痛点。

## Competitive Landscape

替代方式包括传统扑克应用、本地规则机器人和其他终端游戏。尚未做竞品研究，不声称独家功能、首创、最强或优于具体产品。

## Differentiation

- 默认九人桌，八个对手通过真实 `codex exec` 决策，各自加载独立 `SKILL.md`，可接管玩家座位。
- 策略 skill 配合行动加权抽样、位置、可争夺底池与公开统计；不承诺模型水平、胜率或 GTO 能力。
- 显式 `--agent strategic` 可使用程序启发式牌手；它尚未经过强化学习训练，不执行文字 skill。
- 默认“先看翻牌”桌风：普通低成本翻前，所有自动牌手的可选动作排除弃牌和全押，弱起手牌也继续；首次开池最多 3 BB，之后平跟。高成本/短筹码/全押与翻后恢复正常判断，单独记录反复主动全押并调整防守。
- Codex 失败时暂停并允许重试，不自动替换成本地机器人。
- 进行中只向每个模型提供自身底牌与公开信息；结束后向玩家亮出所有牌供复盘。
- B 切换终端隐藏页；小窗口可容纳九个座位。

## Objections

| 顾虑 | 基于实现的回答 |
| --- | --- |
| 对手真是 Codex 吗？ | 默认调用已登录的 Codex CLI；九人真实联调的 18 次动作全部来自 Codex。 |
| 一手要等多久？ | 取决于模型、网络和行动次数；一次短筹码九人验证耗时约 169 秒，只是单次样本。 |
| 模型会偷看其他人的牌吗？ | 决策输入只含自己的底牌、已发公共牌、公开历史与合法动作。 |

**Anti-personas:** 需要真钱、联网真人对战、毫秒级动作或专业训练求解器的用户。

## Switching Dynamics

**Push:** 希望在已有终端中玩牌。
**Pull:** 与实际 Codex 模型同桌、可托管、结束后全牌复盘。
**Habit:** 已有扑克应用可能更熟悉。
**Anxiety:** 账户额度、模型延迟与安装步骤。以上均为产品定位假设。

## Customer Language

使用“终端德州扑克”“Codex 对手”“九人桌”“托管”“亮牌复盘”。避免“稳赚”“职业级”“免费无限量”“完全隐身”“全球首创”。复盘补牌不属于实际结算公共牌，必须标明。

## Brand Voice

中文为主，直接、轻松、具体。可以表达休息时玩一手，不承诺隐藏操作系统进程或外部录制。

## Proof Points

- 自动测试覆盖规则、九人座位、复盘显示、角色注入、边池权益、统计传递、子进程协议和终端交互。
- `validation/codex-nine-seat-smoke.json`：真实九人全 AI 对局完成，18 次 Codex 动作，虚拟筹码守恒。
- `validation/codex-strategy-smoke.json`：加载角色 skill 的三次真实 Codex 场景检查通过，只验证基础判断与接入，不能当作长期强度指标。
- 程序牌手的基线比较保存在 `validation/strategic-benchmark*.json`；必须同时报告样本、基线与区间，不能挑选盈利样本作宣传。
- `docs/table-preview.svg`：确定性牌局生成的界面样例，不是模型实战表现证据。
- 客户、评价、安装量、转化率：暂无。不能编造。

## Goals

**Goal:** 让 GitHub 访问者理解用途并成功开始一手 Codex 对局。
**Primary CTA:** 开始第一手。
**Current metrics:** 未采集。

## Changelog

- v4 (2026-09-08) — 按用户再次反馈将便宜翻前的参与偏好落实为自动牌手的动作约束，三张公共牌后恢复判断；明确 Codex 仍生成实际动作，手动玩家使用正常规则。
- v3 (2026-09-08) — 按用户反馈放宽小注入池、加入反复全押识别与防守，区分活动度测试和盈利能力，避免将旧强度结果套用到新桌风。
- v2 (2026-09-08) — 加入独立角色 skill、决策辅助、程序牌手及评测边界，避免将增强提示词或 Monte Carlo 当作训练好的强化学习模型。
- v1 (2026-09-08) — 根据九人桌、真实 Codex 联调与全牌复盘实现建立定位、文案边界和验证依据。

使用 [marketingskills](https://github.com/coreyhaines31/marketingskills) 的 `product-marketing` 与 `copywriting`，参考版本 `5b2c0007766c6a1cf1d53fd8fc73e979e0821022`。
