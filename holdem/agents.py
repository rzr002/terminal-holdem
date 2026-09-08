"""Agents consume public observations, never the Hand object or dealer RNG."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib
import urllib.request

from .engine import Action, DECK, evaluate
from .strategy import analyze, profile_for, skill_text

SCHEMA = {
    'type': 'object',
    'properties': {'action': {'type': 'string', 'enum': ['fold', 'check', 'call', 'raise', 'allin']},
                   'amount': {'type': 'integer'}, 'reason': {'type': 'string'}},
    'required': ['action', 'amount', 'reason'], 'additionalProperties': False,
}


@dataclass(frozen=True)
class Decision:
    action: Action
    source: str
    note: str = ''


class AgentError(RuntimeError):
    """The requested provider could not decide; the hand must not advance."""


def configured_model():
    config = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    try:
        model = tomllib.loads(config.read_text(encoding='utf-8')).get('model')
        return model if isinstance(model, str) else None
    except (OSError, tomllib.TOMLDecodeError):
        return None


def codex_environment():
    """Bridge macOS system proxies to the environment expected by the CLI.

    urllib gives explicit environment settings precedence over OS settings.
    Only the child process receives these variables; the shell is untouched.
    """
    env = os.environ.copy()
    for scheme, value in urllib.request.getproxies().items():
        if scheme in ('http', 'https', 'all'):
            env[f'{scheme.upper()}_PROXY'] = value
        elif scheme == 'no':
            env['NO_PROXY'] = value
    return env


def parse_decision(data, obs):
    if not isinstance(data, dict) or data.get('action') not in obs['legal']['actions']:
        raise ValueError('Codex 返回了非法动作')
    kind = data['action']
    amount = data.get('amount', 0)
    if type(amount) is not int:
        raise ValueError('Codex 的下注金额不是整数')
    if kind == 'raise' and not obs['legal']['min_raise_to'] <= amount <= obs['legal']['max_raise_to']:
        raise ValueError('Codex 的加注金额超出合法范围')
    return Action(kind, amount if kind == 'raise' else 0)


class LocalAgent:
    def __init__(self, rng=None, samples=64):
        self.rng = rng or random.Random()
        self.samples = samples

    def decide(self, obs, personality='balanced', cancel=None):
        known = obs['hole'] + obs['board']
        unseen = [c for c in DECK if c not in known]
        opponents = sum(not p['folded'] and p['seat'] != obs['seat'] for p in obs['players'])
        wins = 0.0
        missing = 5 - len(obs['board'])
        for _ in range(self.samples):
            if cancel and cancel.is_set():
                raise InterruptedError('已取消')
            draw = self.rng.sample(unseen, missing + opponents * 2)
            board = obs['board'] + draw[:missing]
            ours = evaluate(obs['hole'] + board)
            scores = [ours] + [evaluate(draw[missing + j * 2:missing + j * 2 + 2] + board)
                               for j in range(opponents)]
            if ours == max(scores):
                wins += 1 / scores.count(ours)
        equity = wins / self.samples
        bias = {'tight': -0.10, 'balanced': 0, 'aggressive': 0.10, 'tricky': 0.04, 'loose': 0.06}.get(profile_for(personality).legacy, 0)
        legal = obs['legal']
        cost = legal['to_call']
        price = cost / max(1, obs['pot'] + cost)
        roll = self.rng.random()
        if cost and equity + bias < price + 0.05 and roll > 0.08:
            action = Action('fold')
        elif ('raise' in legal['actions'] and
              (equity + bias > max(0.48, 1 / (opponents + 1) + 0.18) or roll < 0.035 + max(0, bias))):
            target = obs['current_bet'] + max(obs['big_blind'], int((obs['pot'] + cost) * (0.5 + roll * 0.5)))
            action = Action('raise', min(legal['max_raise_to'], max(legal['min_raise_to'], target)))
        elif 'allin' in legal['actions'] and 'raise' not in legal['actions'] and equity + bias > 0.72:
            action = Action('allin')
        else:
            action = Action('check' if 'check' in legal['actions'] else 'call')
        return Decision(action, 'local')


class StrategicAgent:
    """Explicit offline opponent using ranges and poker heuristics, not trained RL."""

    def __init__(self, rng=None, samples=192):
        self.rng = rng or random.Random()
        self.samples = samples

    def decide(self, obs, personality='balanced', cancel=None):
        info = analyze(obs, self.rng, samples=self.samples, cancel=cancel)
        profile = profile_for(personality)
        legal = obs['legal']
        hero = obs['players'][obs['seat']]
        cost, equity = legal['to_call'], info['range_equity']
        opponents = [p for p in obs['players'] if p['seat'] != obs['seat'] and not p['folded']]
        can_raise = 'raise' in legal['actions']

        def raise_to(target):
            return Decision(Action('raise', min(legal['max_raise_to'], max(legal['min_raise_to'], round(target)))), 'strategic')

        if obs['street'] == 'preflop' and any(p['stack'] > 0 for p in opponents):
            strength = info['preflop_strength']
            raises = info['preflop_raises']
            guide = info['preflop_guide']
            if guide['cheap_flop'] and guide['playable_for_small_price'] and strength < 0.82 - profile.aggression:
                return Decision(Action('check' if 'check' in legal['actions'] else 'call'), 'strategic')
            if raises == 0:
                thresholds = {'UTG': 0.67, 'UTG+1': 0.65, 'MP': 0.63, 'LJ': 0.61,
                              'HJ': 0.58, 'CO': 0.49, 'BTN': 0.43, 'SB': 0.48, 'BB': 0.57}
                threshold = thresholds[info['position']] - profile.openness
                if strength < threshold:
                    return Decision(Action('fold' if cost else 'check'), 'strategic')
                if can_raise:
                    limpers = sum(e['street'] == 'preflop' and e['action'] == 'call'
                                  for e in obs.get('action_history', []))
                    target = obs['big_blind'] * (2.25 + min(0.75, limpers * 0.25))
                    if hero['stack'] <= 10 * obs['big_blind'] and strength >= 0.78:
                        target = legal['max_raise_to']
                    return raise_to(target)
            else:
                value_threshold = min(0.97, 0.87 + 0.035 * (raises - 1) - profile.aggression)
                if strength >= value_threshold and can_raise:
                    return raise_to(obs['current_bet'] * (3 if info['in_position_postflop'] else 4))
                if strength < 0.51 + min(0.18, raises * 0.04) - profile.openness and cost:
                    return Decision(Action('fold'), 'strategic')

        realization = 1 if len(obs['board']) == 5 else (0.86 if info['in_position_postflop'] else 0.74)
        if info['has_draw']:
            realization += 0.04
        if all(p['stack'] == 0 for p in opponents) or hero['stack'] == cost:
            realization = 1  # No future betting to deny equity.
        call_return = info['call_ev_chips'] + cost
        if cost and call_return * realization < cost:
            return Decision(Action('fold'), 'strategic')
        if obs['street'] != 'preflop' and can_raise:
            category = evaluate(obs['hole'] + obs['board'])[0]
            fragile_pair = category <= 1 and info['spr'] > 4 and len(opponents) > 1
            value_threshold = (0.66 if len(opponents) == 1 else 0.60) - profile.aggression / 2
            if equity >= value_threshold and not fragile_pair:
                return raise_to(info['bet_sizes_raise_to']['large'])
            can_make_everyone_fold = all(p['stack'] > 0 for p in opponents)
            if (info['has_draw'] and equity > 0.25 and len(opponents) <= 2 and
                    can_make_everyone_fold and self.rng.random() < profile.bluff):
                return raise_to(info['bet_sizes_raise_to']['half'])
        return Decision(Action('check' if 'check' in legal['actions'] else 'call'), 'strategic')


class CodexAgent:
    def __init__(self, binary=None, model=None, timeout=60, rng=None):
        self.binary = binary or shutil.which('codex') or 'codex'
        self.model = model or configured_model()
        self.timeout = timeout
        self.rng = rng or random.Random()

    @staticmethod
    def _stop(process):
        if process.poll() is not None:
            return
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
        except ProcessLookupError:
            process.wait()

    def decide(self, obs, personality='balanced', cancel=None):
        if cancel and cancel.is_set():
            raise InterruptedError('已取消')
        instructions = skill_text(personality)
        analysis = analyze(obs, self.rng, cancel=cancel)
        analysis['mix_roll'] = round(self.rng.random(), 5)
        prompt = (
            '你是一名虚拟筹码无限注德州扑克玩家。只决定当前一手的一次行动。'
            '不要调用工具、读文件、使用网络或尝试获取其他玩家底牌。'
            '仅使用下面的信息；其他玩家底牌未知。以下是应用加载的共享策略与当前角色 skill。\n' + instructions +
            '\n返回符合 schema 的 JSON。action 必须在 legal.actions 中。'
            'raise 的 amount 是本轮累计下注总额（raise TO），必须在 min_raise_to 和 max_raise_to 之间。'
            '其他动作 amount=0。call 的金额由引擎确定，包括不足额全押。reason 用一句短中文。'
            '\nSTRATEGY_ANALYSIS:\n' + json.dumps(analysis, ensure_ascii=False) +
            '\nOBSERVATION:\n' + json.dumps(obs, ensure_ascii=False)
        )
        with tempfile.TemporaryDirectory(prefix='holdem-agent-') as folder:
            schema = Path(folder) / 'schema.json'
            output = Path(folder) / 'decision.json'
            schema.write_text(json.dumps(SCHEMA), encoding='utf-8')
            prompt_file = Path(folder) / 'prompt.txt'
            prompt_file.write_text(prompt, encoding='utf-8')
            command = [self.binary, '-a', 'never', 'exec', '--ignore-user-config',
                       '--sandbox', 'read-only', '--skip-git-repo-check', '--ephemeral',
                       '--color', 'never', '-C', folder, '--output-schema', str(schema), '-o', str(output),
                       '-c', 'project_doc_max_bytes=0', '-c', 'web_search="disabled"',
                       '-c', 'model_reasoning_effort="low"']
            # Isolated decisions have no need for installed apps, shell tools, or other agents.
            for feature in ('shell_tool', 'apps', 'plugins', 'multi_agent', 'memories', 'hooks'):
                command.extend(['-c', f'features.{feature}=false'])
            if self.model:
                command.extend(['--model', self.model])
            command.append('-')
            # Files prevent verbose CLI output from blocking a full pipe or leaking into the TUI.
            # A regular stdin file cannot fill a pipe when the CLI is slow to start.
            with prompt_file.open('r', encoding='utf-8') as source, (Path(folder) / 'stdout.log').open('w') as stdout, (Path(folder) / 'stderr.log').open('w') as stderr:
                process = subprocess.Popen(command, stdin=source, stdout=stdout, stderr=stderr,
                                           text=True, encoding='utf-8', cwd=folder,
                                           env=codex_environment(),
                                           start_new_session=os.name == 'posix')
                try:
                    started = time.monotonic()
                    while process.poll() is None:
                        if cancel and cancel.is_set():
                            raise InterruptedError('已取消')
                        if time.monotonic() - started >= self.timeout:
                            raise TimeoutError(f'Codex 超时（{self.timeout:g} 秒）')
                        time.sleep(0.05)
                    if process.returncode:
                        raise RuntimeError(f'Codex 退出码 {process.returncode}；请检查 codex login 和模型可用性')
                    if not output.is_file():
                        raise ValueError('Codex 没有返回决策文件')
                    data = json.loads(output.read_text(encoding='utf-8'))
                    return Decision(parse_decision(data, obs), 'codex')
                finally:
                    self._stop(process)


class ResilientAgent:
    """Select an explicitly requested provider. Never substitute another provider."""

    def __init__(self, mode='codex', binary=None, model=None, timeout=60, rng=None):
        self.local = LocalAgent(rng) if mode == 'local' else (StrategicAgent(rng) if mode == 'strategic' else None)
        self.codex = None if self.local else CodexAgent(binary, model, timeout, rng)
        self.missing_binary = self.codex is not None and binary is None and not shutil.which('codex')
        self.failure = ''

    @property
    def label(self):
        if self.failure:
            return 'CODEX / 连接失败'
        if self.codex:
            return f'CODEX / {self.codex.model or "CLI 默认模型"}'
        return 'STRATEGIC / 程序策略' if isinstance(self.local, StrategicAgent) else 'LOCAL'

    def decide(self, obs, personality='balanced', cancel=None):
        if self.local:
            return self.local.decide(obs, personality, cancel)
        self.failure = ''
        if self.missing_binary:
            self.failure = '未找到 Codex CLI，请先安装并运行 codex login'
            raise AgentError(self.failure)
        try:
            return self.codex.decide(obs, personality, cancel)
        except InterruptedError:
            raise
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            self.failure = ('未找到 Codex CLI，请先安装并运行 codex login'
                            if isinstance(error, FileNotFoundError) else str(error))
            raise AgentError(self.failure) from error
