"""Run with python3 -m holdem. The default UI uses the terminal's alternate screen."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import locale
import math
import os
import random
import sys
import threading

from .agents import AgentError, CodexAgent, ResilientAgent
from .engine import Hand, MAX_PLAYERS, Player
from .ui import HeadlessUI, PlainUI, CursesUI, QuitGame

NAMES = ['YOU', 'NOVA', 'BLAZE', 'ECHO', 'MOSS', 'ATLAS', 'JADE', 'RAVEN', 'ORBIT']
STYLES = ['balanced', 'tight', 'aggressive', 'tricky', 'loose', 'balanced', 'tight', 'aggressive', 'tricky']


class Application:
    def __init__(self, args):
        self.args = args
        self.rng = random.Random(args.seed)
        self.agent = ResilientAgent(args.agent, model=args.model, timeout=args.timeout,
                                    rng=random.Random(None if args.seed is None else args.seed + 1))
        self.players = [Player(name, args.stack) for name in NAMES[:args.players]]
        self.autoplay = args.autoplay
        self.cancel = threading.Event()
        self.decisions = Counter()
        self.hands_played = 0
        self.hand = None
        self.error = ''

    def run(self, ui):
        button = 0
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='poker-agent')
        try:
            while True:
                h = self.hand = Hand(self.players, button, max(1, self.args.big_blind // 2),
                                     self.args.big_blind, self.rng, self.hands_played + 1)
                while not h.done:
                    if h.actor == 0 and not (self.args.watch or self.autoplay or self.args.headless):
                        action = ui.human_action(self, h)
                        if action is None:
                            continue
                        try:
                            h.act(action)
                        except ValueError as error:
                            if isinstance(ui, CursesUI):
                                ui.notice = str(error)
                            else:
                                print(error)
                            continue
                        self.decisions['human'] += 1
                    else:
                        seat = h.actor
                        future = pool.submit(self.agent.decide, h.observation(seat), STYLES[seat], self.cancel)
                        try:
                            decision = ui.wait_decision(self, h, future)
                        except AgentError as error:
                            self.error = str(error)
                            if not ui.agent_error(self, h, self.error):
                                raise QuitGame
                            continue
                        self.error = ''
                        if seat == 0 and not (self.autoplay or self.args.watch or self.args.headless):
                            continue
                        h.act(decision.action)
                        self.decisions[decision.source] += 1
                        if isinstance(ui, CursesUI):
                            last_action = next(line for line in reversed(h.history) if line.startswith(NAMES[seat] + ' '))
                            ui.notice = f'{decision.source} · {last_action}'
                    ui.render(self, h)
                    ui.wait(self, h, self.args.delay)
                self.hands_played += 1
                funded = [i for i, p in enumerate(self.players) if p.stack]
                limit = self.args.hands or (100 if self.args.headless else 0)
                finished = len(funded) < 2 or (limit > 0 and self.hands_played >= limit)
                ui.after_hand(self, h, finished)
                if finished:
                    break
                button = h.next_button()
        except (QuitGame, KeyboardInterrupt):
            pass
        finally:
            self.cancel.set()
            pool.shutdown(wait=True, cancel_futures=True)
        return {'hands_played': self.hands_played,
                'players': [{'name': p.name, 'stack': p.stack} for p in self.players],
                'unsettled_pot': self.hand.pot if self.hand else 0,
                'decisions': dict(self.decisions), 'agent': self.agent.label,
                'error': self.error}


def parser():
    p = argparse.ArgumentParser(description='终端德州扑克 · Codex 牌手 / 本地策略 · 纯虚拟筹码')
    p.add_argument('--agent', choices=['codex', 'local', 'auto'], default='codex', help='默认 Codex；失败暂停，不替换对手。auto 是 codex 的兼容别名')
    p.add_argument('--model', help='指定 Codex 模型；省略则沿用本机 Codex 配置中的模型')
    p.add_argument('--players', type=int, default=MAX_PLAYERS, help=f'总座位数，含你，2–{MAX_PLAYERS}（默认九人满员桌）')
    p.add_argument('--stack', type=int, default=2000, help='每人初始筹码（默认 2000）')
    p.add_argument('--big-blind', type=int, default=20, help='大盲注（默认 20，小盲为其一半取整）')
    p.add_argument('--watch', action='store_true', help='全 AI 观战，包含你的座位')
    p.add_argument('--autoplay', action='store_true', help='启动时托管你的座位；T 可接管')
    p.add_argument('--plain', action='store_true', help='逐行交互，不使用 curses')
    p.add_argument('--headless', action='store_true', help='全 AI 跑局后输出 JSON；默认最多 100 手')
    p.add_argument('--hands', type=int, default=0, help='最多玩多少手；0 表示打到只剩一人')
    p.add_argument('--seed', type=int, help='固定发牌和本地策略随机种子，便于复现')
    p.add_argument('--delay', type=float, default=0.7, help='TUI 每次行动后的停留秒数')
    p.add_argument('--timeout', type=float, default=60, help='Codex 单次决策超时秒数（默认 60）')
    p.add_argument('--check-codex', action='store_true', help='真实请求一次 Codex 决策，检测连接后退出')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not 2 <= args.players <= MAX_PLAYERS:
        p.error(f'--players 必须是 2–{MAX_PLAYERS}')
    if args.stack < 1 or args.big_blind < 1 or args.hands < 0:
        p.error('筹码和盲注必须为正整数，手数不能为负')
    if not math.isfinite(args.delay) or not math.isfinite(args.timeout) or args.delay < 0 or args.timeout <= 0:
        p.error('--delay 必须为有限非负数，--timeout 必须为有限正数')
    if args.check_codex:
        hand = Hand([Player('YOU', 200), Player('NOVA', 200)])
        try:
            result = CodexAgent(model=args.model, timeout=args.timeout).decide(hand.observation(hand.actor))
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            print(f'Codex 连接失败：{error}', file=sys.stderr)
            return 1
        print(f'Codex 连接成功：{result.action.kind} {result.action.amount}（真实模型决策）')
        return 0
    app = Application(args)
    if args.headless:
        result = app.run(HeadlessUI())
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.plain or not sys.stdin.isatty() or not sys.stdout.isatty() or os.environ.get('TERM') == 'dumb':
        result = app.run(PlainUI())
        print(f"已退出 · 完成 {result['hands_played']} 手 · 筹码仅保存在本次进程中")
    else:
        try:
            import curses
        except ImportError:
            print('此 Python 没有 curses；使用逐行模式。')
            result = app.run(PlainUI())
            return 1 if result['error'] else 0
        locale.setlocale(locale.LC_ALL, '')
        try:
            result = curses.wrapper(lambda screen: app.run(CursesUI(screen)))
        except curses.error:
            print('终端无法初始化，请使用 --plain 或设置 TERM=xterm-256color。', file=sys.stderr)
            return 1
    if result['error']:
        if not args.headless:
            print(f"Codex 未完成决策，牌局已停止：{result['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
