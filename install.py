#!/usr/bin/env python3
"""Install the holdem command for the current user on macOS or Linux."""

import argparse
import os
from pathlib import Path
import shlex
import sys
import tempfile

HEADER = '#!/bin/sh\n# Installed by terminal-holdem/install.py\n'


def install_launcher(bin_dir):
    entrypoint = Path(__file__).resolve().with_name('play.py')
    if not entrypoint.is_file():
        raise FileNotFoundError('缺少 play.py，请下载完整项目后再安装')
    bin_dir = bin_dir.expanduser().resolve()
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / 'holdem'
    if launcher.is_symlink():
        managed = launcher.resolve() == entrypoint
    elif launcher.is_file():
        with launcher.open('rb') as existing:
            managed = existing.read(len(HEADER)) == HEADER.encode()
    else:
        managed = not launcher.exists()
    if not managed:
        raise FileExistsError(f'{launcher} 已被其他文件占用，不会覆盖；可用 --bin-dir 选择其他目录')

    content = HEADER + f'exec {shlex.quote(sys.executable)} {shlex.quote(str(entrypoint))} "$@"\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.holdem-',
                                         dir=bin_dir, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        temporary.chmod(0o755)
        temporary.replace(launcher)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return launcher


def main(argv=None):
    parser = argparse.ArgumentParser(description='安装 holdem 短命令（macOS / Linux，无需 sudo）')
    parser.add_argument('--bin-dir', type=Path, default=Path.home() / '.local/bin',
                        help='命令安装目录，默认 ~/.local/bin')
    args = parser.parse_args(argv)
    if os.name != 'posix':
        parser.error('短命令安装支持 macOS / Linux；其他环境请使用 python3 play.py --plain')
    if sys.version_info < (3, 11):
        parser.error('游戏需要 Python 3.11 或更新版本，请用对应的 Python 运行安装器')
    try:
        launcher = install_launcher(args.bin_dir)
    except OSError as error:
        print(f'安装失败：{error}', file=sys.stderr)
        return 1
    print(f'已安装：{launcher}')
    if str(launcher.parent) not in [os.path.realpath(path) for path in os.get_exec_path()]:
        print('当前终端还需执行：')
        print(f'export PATH={shlex.quote(str(launcher.parent))}:"$PATH"')
        print('把这行加入 ~/.zshrc（zsh）或 ~/.bashrc（Bash），新终端也能使用。')
    print('启动：holdem\n指定模型：holdem --model gpt-5.3-codex-spark')
    print('项目移动或 Python 环境更换后，重新运行 python3 install.py 即可更新入口。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
