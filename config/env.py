"""Минимальная загрузка локального .env без внешней зависимости."""

import os
import re
from pathlib import Path


ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def load_env_file(path, *, override=False):
    """Загружает KEY=VALUE; системное окружение по умолчанию имеет приоритет."""
    path = Path(path)
    if not path.is_file():
        return False
    for line_number, raw_line in enumerate(
        path.read_text(encoding='utf-8-sig').splitlines(), 1,
    ):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, separator, value = line.partition('=')
        name = name.strip()
        if not separator or not ENV_NAME.fullmatch(name):
            raise ValueError(f'Некорректная строка .env: {line_number}.')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if override or name not in os.environ:
            os.environ[name] = value
    return True