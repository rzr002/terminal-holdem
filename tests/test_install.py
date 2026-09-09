"""The public installer must work outside the checkout and preserve other commands."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'the short command targets macOS and Linux')
class InstallTests(unittest.TestCase):
    def install(self, folder, root=ROOT, env=None):
        return subprocess.run([sys.executable, str(root / 'install.py'), '--bin-dir', str(folder)],
                              cwd=folder.parent, env=env, text=True, capture_output=True, timeout=10)

    def test_installed_command_starts_game_from_another_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'bin'
            result = self.install(folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            launcher = folder / 'holdem'
            self.assertTrue(os.access(launcher, os.X_OK))
            result = subprocess.run([str(launcher), '--agent', 'local', '--plain', '--players', '2'],
                                    cwd=temp, input='q\n', capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('HOLD', result.stdout)
            self.assertIn('已退出', result.stdout)

    def test_paths_and_arguments_are_passed_literally(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "游戏 repo ' $(touch should-not-exist)"
            root.mkdir()
            shutil.copyfile(ROOT / 'install.py', root / 'install.py')
            (root / 'play.py').write_text('import json, sys\nprint(json.dumps(sys.argv[1:]))\n')
            folder = Path(temp) / "user bin '"
            result = self.install(folder, root=root)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = ['--model', 'test-model', 'with spaces', '$(touch injected)', "quote's"]
            result = subprocess.run([str(folder / 'holdem'), *args], cwd=temp,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), args)
            self.assertFalse((Path(temp) / 'should-not-exist').exists())
            self.assertFalse((Path(temp) / 'injected').exists())

    def test_reinstall_refreshes_own_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'bin'
            first = self.install(folder)
            self.assertEqual(first.returncode, 0, first.stderr)
            content = (folder / 'holdem').read_text()
            second = self.install(folder)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((folder / 'holdem').read_text(), content)
            self.assertEqual(list(folder.iterdir()), [folder / 'holdem'])

    def test_existing_unrelated_command_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            launcher = folder / 'holdem'
            launcher.write_text('another application\n')
            result = self.install(folder)
            self.assertEqual(result.returncode, 1)
            self.assertIn('不会覆盖', result.stderr)
            self.assertEqual(launcher.read_text(), 'another application\n')

    def test_foreign_symlink_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            other = folder / 'other-app'
            other.write_text('another application\n')
            (folder / 'holdem').symlink_to(other)
            result = self.install(folder)
            self.assertEqual(result.returncode, 1)
            self.assertEqual((folder / 'holdem').readlink(), other)
            self.assertEqual(other.read_text(), 'another application\n')

    def test_legacy_project_symlink_can_be_upgraded(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'holdem').symlink_to(ROOT / 'play.py')
            result = self.install(folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((folder / 'holdem').is_symlink())
            self.assertTrue((ROOT / 'play.py').is_file())

    def test_missing_path_entry_has_actionable_instructions(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'bin'
            result = self.install(folder, env=dict(os.environ, PATH='/usr/bin:/bin'))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('export PATH=', result.stdout)
            self.assertIn('.zshrc', result.stdout)
            self.assertIn('.bashrc', result.stdout)
