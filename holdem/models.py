"""Read model metadata from the local Codex cache without changing its settings."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class ModelOption:
    model: str
    description: str = ''


def validate_model(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', value):
        raise ValueError('模型 ID 只能包含字母、数字、点、下划线、冒号、斜线和短横线')
    return value


def available_models(current=None):
    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    try:
        data = json.loads((home / 'models_cache.json').read_text(encoding='utf-8'))
        rows = data.get('models', []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        rows = []
    if not isinstance(rows, list):
        rows = []
    rows = [row for row in rows if isinstance(row, dict) and row.get('visibility') == 'list']
    rows.sort(key=lambda row: row['priority'] if type(row.get('priority')) is int else 999)
    options, seen = [], set()
    for row in rows:
        try:
            model = validate_model(row.get('slug'))
        except ValueError:
            continue
        if model not in seen:
            description = row.get('description', '')
            options.append(ModelOption(model, description if isinstance(description, str) else ''))
            seen.add(model)
    if current and current not in seen:
        options.insert(0, ModelOption(current, '当前配置'))
    return options


def resolve_model_choice(text, options, current):
    text = text.strip()
    if not text:
        return current
    if text.isdecimal():
        number = int(text)
        if 1 <= number <= len(options):
            return options[number - 1].model
        raise ValueError('请输入列表中的编号，或直接输入模型 ID')
    return validate_model(text)
