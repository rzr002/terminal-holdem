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

PERSONALITIES = {
    'balanced': '均衡型：根据底池赔率、位置和牌力决策，适度诈唬。',
    'tight': '稳健型：选择性入池，重视价值下注，避免用弱牌支付大额下注。',
    'aggressive': '进攻型：善用位置和半诈唬施压，但不无脑全押。',
    'tricky': '灵活型：混合慢打、价值下注和小概率诈唬，保持难以预测。',
    'loose': '宽松型：喜欢看翻牌，但会在明显不利的大额下注前弃牌。',
}
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
        bias = {'tight': -0.10, 'balanced': 0, 'aggressive': 0.10, 'tricky': 0.04, 'loose': 0.06}.get(personality, 0)
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


class CodexAgent:
    def __init__(self, binary=None, model=None, timeout=60):
        self.binary = binary or shutil.which('codex') or 'codex'
        self.model = model or configured_model()
        self.timeout = timeout

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
        prompt = (
            '你是一名虚拟筹码无限注德州扑克玩家。只决定当前一手的一次行动。'
            '不要调用工具、读文件、使用网络或尝试获取其他玩家底牌。'
            '仅使用下面的信息；其他玩家底牌未知。' + PERSONALITIES.get(personality, PERSONALITIES['balanced']) +
            '\n返回符合 schema 的 JSON。action 必须在 legal.actions 中。'
            'raise 的 amount 是本轮累计下注总额（raise TO），必须在 min_raise_to 和 max_raise_to 之间。'
            '其他动作 amount=0。call 的金额由引擎确定，包括不足额全押。reason 用一句短中文。'
            '\nOBSERVATION:\n' + json.dumps(obs, ensure_ascii=False)
        )
        with tempfile.TemporaryDirectory(prefix='holdem-agent-') as folder:
            schema = Path(folder) / 'schema.json'
            output = Path(folder) / 'decision.json'
            schema.write_text(json.dumps(SCHEMA), encoding='utf-8')
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
            with (Path(folder) / 'stdout.log').open('w') as stdout, (Path(folder) / 'stderr.log').open('w') as stderr:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                           text=True, encoding='utf-8', cwd=folder,
                                           env=codex_environment(),
                                           start_new_session=os.name == 'posix')
                try:
                    process.stdin.write(prompt)
                    process.stdin.close()
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
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
                    self._stop(process)


class ResilientAgent:
    """Select an explicitly requested provider. Never substitute another provider."""

    def __init__(self, mode='codex', binary=None, model=None, timeout=60, rng=None):
        self.local = LocalAgent(rng) if mode == 'local' else None
        self.codex = None if mode == 'local' else CodexAgent(binary, model, timeout)
        self.missing_binary = mode != 'local' and binary is None and not shutil.which('codex')
        self.failure = ''

    @property
    def label(self):
        if self.failure:
            return 'CODEX / 连接失败'
        return f'CODEX / {self.codex.model or "CLI 默认模型"}' if self.codex else 'LOCAL'

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
