"""Chinese terminal UI; curses stays responsive during agent decisions."""

import time
import unicodedata

from .engine import Action, HAND_NAMES, STREET_NAMES, evaluate
from .models import available_models, resolve_model_choice, validate_model
from .strategy import PROFILES

SUIT_GLYPHS = {'s': '♠', 'h': '♥', 'd': '♦', 'c': '♣'}


class QuitGame(Exception):
    pass


def clip(text, width):
    result, cells = '', 0
    for ch in text:
        if unicodedata.category(ch).startswith('C'):
            continue
        size = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in 'WF' else 1)
        if cells + size > width:
            break
        result += ch
        cells += size
    return result


def cards_text(cards):
    return ' '.join(f'[{c[0]}{SUIT_GLYPHS[c[1]]}]' for c in cards)


def parse_action(text, legal):
    parts = text.lower().strip().split()
    if parts and parts[0] in ('r', 'raise') and len(parts) == 2:
        try:
            return Action('raise', int(parts[1]))
        except ValueError:
            pass
    if len(parts) == 1:
        kind = {'f': 'fold', 'fold': 'fold', 'c': 'check' if 'check' in legal['actions'] else 'call',
                'check': 'check', 'call': 'call', 'a': 'allin', 'allin': 'allin'}.get(parts[0])
        if kind:
            return Action(kind)
    raise ValueError('输入 F 弃牌 / C 过牌或跟注 / R 金额 加注至 / A 全押')


def frame_lines(hand, width, provider, autoplay, boss, status, height=None):
    if boss:
        return ['service-monitor  /  local', '', 'STATUS    SERVICE          HEALTH',
                'running   scheduler        ok', 'running   worker-pool      ok',
                'idle      file-indexer     ok', '', 'All services healthy.', '',
                'Waiting for events...']
    rule = '─' * max(0, min(width - 4, 100))
    mode = 'AI 托管' if autoplay else '手动玩家'
    table_style = '基线桌' if provider == 'LOCAL' else '先看翻牌'
    lines = ['  ♠  HOLD’EM     /     AFTER HOURS',
             f'  {len(hand.players)} 人桌 · {table_style} · {provider} · {mode}', '  ' + rule,
             f'  HAND {hand.number:03d}     {STREET_NAMES[hand.street]}     盲注 {hand.small_blind}/{hand.big_blind}', '']
    board = hand.review_board if hand.done else hand.board
    supplemented = hand.done and len(hand.board) < 5
    visible = board + [None] * (5 - len(board))
    marks = ['*' if supplemented and i >= len(hand.board) else ' ' for i in range(5)]
    lines.append('    ' + ' '.join('┌─────┐' for _ in visible))
    lines.append('    ' + ' '.join(f'│ {c[0]}{SUIT_GLYPHS[c[1]]}{marks[i]} │' if c else '│  ·  │'
                                 for i, c in enumerate(visible)))
    lines.append('    ' + ' '.join('└─────┘' for _ in visible))
    summary = (('    本手已结算 · * 补牌未参与结算' if supplemented else
                '    本手已结算 · 公共牌与全部底牌已公开') if hand.done else
               f'    POT  {hand.pot:,}      当前下注  {hand.current_bet:,}')
    lines += [summary, '',
              '  座位 / 风格          筹码       本轮    手牌 / 动作']
    styles = ['你  '] + [profile.style for profile in PROFILES[1:]]
    for i, p in enumerate(hand.players):
        marker = '▶' if hand.actor == i else ' '
        badges = ('D' if i == hand.button else '') + ('s' if i == hand.sb else '') + ('b' if i == hand.bb else '')
        shown = i == 0 or hand.done
        hole = (cards_text(p.hole) if shown else '[··] [··]') if p.hole else '—'
        state = p.last_action
        if not p.hole:
            state = '已出局'
        if hand.done and hand.showdown and not p.folded:
            state = HAND_NAMES[evaluate(p.hole + hand.board)[0]]
        lines.append(f' {marker}{p.name:<6} {badges:<3} {styles[i]}   {p.stack:>6,}    {p.bet:>6,}    {hole}  {state}')
    lines += ['', '  ' + rule]
    if hand.done:
        lines.extend('  ' + line for line in hand.results)
    else:
        lines.extend('  ' + line for line in hand.history[-3:])
    if height is None and status:
        lines += ['', '  ' + status]
    if height is not None:
        capacity = height - 6
        if len(lines) > capacity:
            # Compact board leaves room for every seat and payout on a 24-row terminal.
            seats = lines[11:11 + len(hand.players)]
            lines = [lines[0], lines[1], lines[3],
                     '  公共牌  ' + (' '.join(cards_text([c]) + marks[i].strip()
                                            for i, c in enumerate(board)) or '等待翻牌'), lines[8], lines[10],
                     *seats, '  ' + rule]
            tail = hand.results if hand.done else hand.history
            room = max(0, capacity - len(lines))
            lines.extend('  ' + line for line in tail[-room:] if room)
        lines = lines[:capacity]
    return lines


class PlainUI:
    def show_models(self, current):
        options = available_models(current)
        print('\n选择 Codex 模型 · 当前：' + (current or 'CLI 默认模型'))
        print('候选来自本机 Codex 缓存；实际可用性以账户为准。')
        for i, option in enumerate(options, 1):
            mark = ' *' if option.model == current else ''
            print(clip(f'  {i}. {option.model}{mark}  {option.description}', 110))
        if not options:
            print('暂无缓存列表，可直接输入模型 ID，或 Enter 沿用 CLI 默认模型。')
        return options

    def pick_model(self, current):
        options = self.show_models(current)
        while True:
            try:
                answer = input('编号 / 模型 ID / Enter 保持当前 / Q 退出 > ').strip()
            except EOFError:
                raise QuitGame
            if answer.lower() == 'q':
                raise QuitGame
            try:
                return resolve_model_choice(answer, options, current)
            except ValueError as error:
                print(error)

    def change_model(self, app):
        if not app.agent.codex:
            print('当前为程序对手；使用 --agent codex 启动后可选择模型')
            return
        model = self.pick_model(app.pending_model or app.agent.codex.model)
        print(app.request_model(model))

    def agent_error(self, app, hand, message):
        print(f'Codex 未完成决策：{message}。当前行动保持不变。')
        try:
            command = input('R 重试 Codex / M 换模型并重试 / Q 退出 > ').strip().lower()
            if command == 'm':
                self.change_model(app)
                return True
            return command == 'r'
        except EOFError:
            return False

    def render(self, app, hand, status=''):
        print('\n'.join(frame_lines(hand, 100, app.agent.label, app.autoplay or app.args.watch, False, status)))

    def human_action(self, app, hand):
        self.render(app, hand, '轮到你了')
        print('F 弃牌 | C 过牌/跟注 | R 120 加注至120 | A 全押 | T 托管 | M 模型 | Q 退出')
        legal = hand.legal()
        print(f"需跟注 {legal['to_call']}；最小加注至 {legal['min_raise_to']}；最大 {legal['max_raise_to']}")
        while True:
            try:
                command = input('> ').strip().lower()
            except EOFError:
                raise QuitGame
            if command == 'q':
                raise QuitGame
            if command == 't':
                app.autoplay = True
                return None
            if command == 'm':
                self.change_model(app)
                return None
            try:
                return parse_action(command, legal)
            except ValueError as error:
                print(error)

    def wait_decision(self, app, hand, future):
        print(f'{hand.players[hand.actor].name} 正在思考 · {app.agent.label}', flush=True)
        while not future.done():
            time.sleep(0.05)
        return future.result()

    def wait(self, app, hand, seconds):
        pass

    def after_hand(self, app, hand, finished):
        self.render(app, hand, app.end_reason if finished else '本手结束')
        if finished or app.args.watch or app.autoplay:
            return
        try:
            while True:
                command = input('Enter 下一手 / M 模型 / Q 退出 > ').strip().lower()
                if command == 'q':
                    raise QuitGame
                if command != 'm':
                    return
                self.change_model(app)
        except EOFError:
            raise QuitGame


class HeadlessUI(PlainUI):
    def agent_error(self, app, hand, message):
        return False

    def render(self, app, hand, status=''):
        pass

    def wait_decision(self, app, hand, future):
        return future.result()

    def after_hand(self, app, hand, finished):
        pass


class CursesUI:
    def agent_error(self, app, hand, message):
        self.notice = 'M 换模型后按 R 重试 / Q 退出'
        while True:
            self.render(app, hand, 'Codex 未完成决策 · 牌局暂停')
            key = self._key(app)
            if not self.boss and not self.paused and key == 'r':
                self.notice = '正在重试 Codex'
                return True

    def __init__(self, screen):
        import curses
        self.curses = curses
        self.screen = screen
        self.boss = self.paused = False
        self.notice = ''
        self.raise_text = None
        screen.keypad(True)
        screen.timeout(80)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.color = curses.has_colors()
        if self.color:
            curses.start_color()
            try:
                curses.use_default_colors()
                background = -1
            except curses.error:
                background = curses.COLOR_BLACK
            for number, color in [(1, curses.COLOR_CYAN), (2, curses.COLOR_YELLOW),
                                  (3, curses.COLOR_GREEN), (4, curses.COLOR_RED)]:
                curses.init_pair(number, color, background)

    def _write(self, row, text, color=0, bold=False):
        height, width = self.screen.getmaxyx()
        if not 0 <= row < height:
            return
        attr = self.curses.color_pair(color) if self.color and color else 0
        if bold:
            attr |= self.curses.A_BOLD
        try:
            self.screen.addstr(row, 0, clip(text, max(0, width - 1)), attr)
        except self.curses.error:
            pass

    def change_model(self, app):
        if not app.agent.codex:
            self.notice = '当前为程序对手；使用 --agent codex 启动后可选择模型'
            return
        current = app.pending_model or app.agent.codex.model
        options = available_models(current)
        selected = next((i for i, item in enumerate(options) if item.model == current), 0)
        entered, error = None, ''
        while True:
            if self.boss:
                self.render(app, app.hand)
            else:
                self.screen.erase()
                height, _ = self.screen.getmaxyx()
                self._write(0, '  选择 Codex 模型', 1, True)
                self._write(1, f'  当前：{app.agent.codex.model or "CLI 默认模型"}')
                self._write(2, '  下一次决策生效 · 当前请求会完成 · 保留牌局', 2)
                labels = [f'{item.model}  {item.description}' for item in options] + ['手动输入模型 ID']
                capacity = max(1, height - 9)
                start = max(0, selected - capacity + 1)
                for row, i in enumerate(range(start, min(len(labels), start + capacity)), 4):
                    self._write(row, f'  {">" if selected == i else " "} {i + 1}. {labels[i]}',
                                3 if selected == i else 0)
                self._write(height - 4, '  模型 ID > ' + entered if entered is not None else
                            '  ↑↓ / 编号选择 · Enter 确认 · I 手动输入 · Esc 返回', 2)
                self._write(height - 3, '  ' + (error or '列表来自 Codex 缓存；实际可用性以账户为准'))
                self._write(height - 2, '  Ctrl-B 隐藏 / 恢复 · Ctrl-C 退出' if entered is not None else
                            '  B 隐藏 / 恢复 · Q 退出', 1)
                self.screen.refresh()
            try:
                key = self.screen.get_wch()
            except self.curses.error:
                continue
            if key in ('\x03', '\x04') or (entered is None and key in ('q', 'Q')):
                raise QuitGame
            if key == '\x02' or ((entered is None or self.boss) and key in ('b', 'B')):
                self.boss = not self.boss
                self.screen.clear()
                continue
            if self.boss:
                continue
            if key == '\x1b':
                if entered is None:
                    return
                entered, error = None, ''
            elif key in ('\n', '\r', self.curses.KEY_ENTER):
                if entered is not None:
                    try:
                        model = validate_model(entered.strip())
                    except ValueError as invalid:
                        error = str(invalid)
                        continue
                elif selected == len(options):
                    entered = ''
                    continue
                else:
                    model = options[selected].model
                self.notice = app.request_model(model)
                return
            elif entered is not None:
                if key in ('\x7f', '\b', self.curses.KEY_BACKSPACE):
                    entered = entered[:-1]
                elif isinstance(key, str) and key.isprintable() and len(entered) < 128:
                    entered += key
            elif key in (self.curses.KEY_UP, 'k'):
                selected = (selected - 1) % (len(options) + 1)
            elif key in (self.curses.KEY_DOWN, 'j'):
                selected = (selected + 1) % (len(options) + 1)
            elif key in ('i', 'I'):
                entered = ''
            elif isinstance(key, str) and key in '123456789' and int(key) <= len(options) + 1:
                selected = int(key) - 1

    def render(self, app, hand, status=''):
        screen = self.screen
        height, width = screen.getmaxyx()
        screen.erase()
        if self.paused and not self.boss:
            status = '已暂停 · P 继续'
        lines = frame_lines(hand, width, app.agent.label, app.autoplay or app.args.watch, self.boss, status, height)
        if self.boss:
            for row, line in enumerate(lines[:height - 1]):
                self._write(row, line)
            screen.refresh()
            return
        if height < 24 or width < 72:
            self._write(0, '请将终端扩大到至少 72 × 24（推荐 100 × 36）', 2)
            self._write(2, 'B 隐藏 / Q 退出；扩大窗口后自动恢复')
            screen.refresh()
            return
        # Keep controls anchored to the bottom, even after terminal resizing.
        for row, line in enumerate(lines[:height - 6]):
            color = 3 if line.startswith(' ▶') else (1 if row < 3 else 0)
            self._write(row, line, color, row == 0 or color == 3)
        self._write(height - 6, '  ' + status, 2, True)
        legal = hand.legal()
        if app.error:
            self._write(height - 5, '  R 重试 Codex / M 换模型 / Q 退出', 2)
        elif hand.actor == 0 and not hand.done:
            actions = legal['actions']
            call = f"C 跟注 {legal['to_call']}" if legal['to_call'] else 'C 过牌'
            options = ['F 弃牌', call]
            if 'raise' in actions:
                options.append(f"R 加注至 {legal['min_raise_to']}–{legal['max_raise_to']}")
            if 'allin' in actions:
                options.append('A 全押')
            self._write(height - 5, '  ' + '  /  '.join(options), 2)
        else:
            if hand.done:
                controls = 'Q / Enter 退出' if app.end_reason else 'N / Enter 下一手'
            else:
                controls = '对手行动中；随时可隐藏或暂停'
            self._write(height - 5, '  ' + controls)
        note = f'加注至 > {self.raise_text}  （Enter 确认 / Esc 取消）' if self.raise_text is not None else self.notice
        if app.pending_model:
            note = f'下一次决策使用 {app.pending_model} · 等待当前决策完成'
        self._write(height - 4, '  ' + note, 2 if self.raise_text is not None else 0)
        self._write(height - 3, '  ' + (app.agent.failure or 'D 庄家 · s 小盲 · b 大盲 · 每手结束公开全部底牌'), 4 if app.agent.failure else 0)
        self._write(height - 2, '  T 托管  M 模型  P 暂停  B 隐藏/恢复  ? 帮助  Q 退出', 1)
        screen.refresh()

    def _key(self, app):
        try:
            key = self.screen.get_wch()
        except self.curses.error:
            return None
        if isinstance(key, str):
            key = key.lower()
        if key in ('q', '\x03', '\x04'):
            raise QuitGame
        if key == 'b':
            self.boss = not self.boss
            self.screen.clear()
            return None
        if self.boss:
            return None
        if key == 'm':
            self.raise_text = None
            self.change_model(app)
            return None
        if key == 'p':
            self.paused = not self.paused
            return None
        if key == 't':
            if not app.args.watch:
                app.autoplay = not app.autoplay
                self.notice = '托管已开启' if app.autoplay else '托管已关闭，当前 AI 决策完成后交还控制'
            else:
                self.notice = '观战模式全部由 AI 操作；P 可暂停'
            return None
        if key == '?':
            self.notice = 'C 过牌/跟注；R 本轮总额；A 全押；M 换模型；B 隐藏'
            return None
        return None if self.paused else key

    def human_action(self, app, hand):
        self.raise_text = None
        while True:
            self.render(app, hand, '轮到你了 · 选择一个动作')
            key = self._key(app)
            if self.boss or self.paused:
                continue
            if app.autoplay:
                self.raise_text = None
                return None
            if self.raise_text is not None:
                if key == '\x1b':
                    self.raise_text = None
                elif key in ('\n', '\r', self.curses.KEY_ENTER):
                    try:
                        action = parse_action('r ' + self.raise_text, hand.legal())
                        self.raise_text = None
                        return action
                    except ValueError:
                        self.notice = '请输入整数金额'
                elif key in ('\x7f', '\b', self.curses.KEY_BACKSPACE):
                    self.raise_text = self.raise_text[:-1]
                elif isinstance(key, str) and key in '0123456789' and len(self.raise_text) < 12:
                    self.raise_text += key
            elif key == 'r':
                if 'raise' in hand.legal()['actions']:
                    self.raise_text = ''
                else:
                    self.notice = '当前不能加注'
            elif key in ('f', 'c', 'a'):
                return parse_action(key, hand.legal())

    def wait_decision(self, app, hand, future):
        started = time.monotonic()
        spinner = '◐◓◑◒'
        while not future.done() or self.boss or self.paused:
            elapsed = time.monotonic() - started
            icon = spinner[int(elapsed * 5) % len(spinner)]
            self.render(app, hand, f'{icon} {hand.players[hand.actor].name} 思考中 · {app.agent.label} · {elapsed:.1f}s')
            self._key(app)
        return future.result()

    def wait(self, app, hand, seconds):
        remaining = seconds
        while remaining > 0 or self.boss or self.paused:
            before = time.monotonic()
            was_paused = self.boss or self.paused
            self.render(app, hand, '已暂停 · P 继续' if self.paused else '牌局进行中')
            self._key(app)
            if not was_paused and not self.boss and not self.paused:
                remaining -= time.monotonic() - before

    def after_hand(self, app, hand, finished):
        if (app.args.watch or app.autoplay) and not finished:
            self.wait(app, hand, max(1.5, app.args.delay * 2))
            return
        status = f'{app.end_reason} · Q / Enter 退出' if finished else '本手结束 · N / Enter 下一手'
        while True:
            self.render(app, hand, status)
            key = self._key(app)
            if not self.boss and not self.paused and (key in ('\n', '\r', self.curses.KEY_ENTER)
                                                     or (key == 'n' and not finished)):
                return
