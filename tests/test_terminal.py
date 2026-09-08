"""Exercise real curses, resizing, and cancellation in a drained pseudo-terminal."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == 'posix', 'curses PTY integration requires POSIX')
class TerminalTests(unittest.TestCase):
    def test_model_selection_switch_hide_resize_and_quit_during_agent_request(self):
        import fcntl
        import pty
        import struct
        import termios

        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / 'codex'
            fake.write_text(f'#!{sys.executable}\n' + '''import os, sys, time
from pathlib import Path
home = Path(os.environ['CODEX_HOME'])
model = sys.argv[sys.argv.index('--model') + 1]
with (home / 'requests.txt').open('a') as log:
    log.write(model + '\\n')
if model == 'test-original':
    while not (home / 'release').exists():
        time.sleep(0.05)
else:
    time.sleep(20)
Path(sys.argv[sys.argv.index('-o') + 1]).write_text('{"action":"call","amount":0}')
''')
            fake.chmod(0o755)
            Path(folder, 'config.toml').write_text('model = "test-original"\n')
            Path(folder, 'models_cache.json').write_text(json.dumps({'models': [
                {'slug': 'test-original', 'visibility': 'list'},
                {'slug': 'test-fast', 'visibility': 'list'}]}))
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 100, 0, 0))
            env = dict(os.environ, TERM='xterm-256color', CODEX_HOME=folder,
                       PATH=folder + os.pathsep + os.environ['PATH'])
            process = subprocess.Popen(
                [sys.executable, '-m', 'holdem', '--agent', 'codex', '--watch', '--timeout', '25'],
                stdin=slave, stdout=slave, stderr=slave, env=env,
                cwd=Path(__file__).resolve().parents[1], start_new_session=True)
            os.close(slave)

            def drain_until(needle=None, seconds=3):
                data = b''
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if select.select([master], [], [], 0.05)[0]:
                        try:
                            data += os.read(master, 65536)
                        except OSError:
                            break
                    if needle and needle.encode() in data:
                        return data
                    if needle is None and process.poll() is not None:
                        return data
                if needle:
                    self.fail(f'No {needle!r} in terminal output: {data[-400:]!r}')
                return data

            try:
                drain_until('编号 / 模型 ID')
                os.write(master, b'1\n')
                drain_until('思考中')
                os.write(master, b'm')
                drain_until('选择 Codex 模型')
                os.write(master, b'i')
                drain_until('模型 ID >')
                os.write(master, b'test-fast\n')
                drain_until('下一次决策使用')
                Path(folder, 'release').touch()
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    drain_until(seconds=0.1)
                    log = Path(folder, 'requests.txt')
                    if log.exists() and log.read_text().splitlines() == ['test-original', 'test-fast']:
                        break
                self.assertEqual(log.read_text().splitlines(), ['test-original', 'test-fast'])
                os.write(master, b'b')
                hidden = drain_until('Waiting for events...')
                self.assertNotIn(b'HOLD', hidden.split(b'\x1b[2J')[-1])
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
                os.kill(process.pid, signal.SIGWINCH)
                os.write(master, b'b')
                drain_until('HOLD')
                os.write(master, b'q')
                # A real terminal keeps reading. Waiting without draining can fill the PTY
                # output buffer during a resize and block curses before it reads Q.
                output = drain_until(seconds=4)
                self.assertEqual(process.wait(timeout=1), 0, output[-1000:])
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
                os.close(master)
