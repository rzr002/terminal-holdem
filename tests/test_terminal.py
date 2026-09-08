"""Exercise real curses, resizing, and cancellation in a drained pseudo-terminal."""

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
    def test_hide_resize_and_quit_during_active_agent_request(self):
        import fcntl
        import pty
        import struct
        import termios

        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / 'codex'
            fake.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(20)\n')
            fake.chmod(0o755)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 32, 100, 0, 0))
            env = dict(os.environ, TERM='xterm-256color', PATH=folder + os.pathsep + os.environ['PATH'])
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
                drain_until('思考中')
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
