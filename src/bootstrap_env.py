"""
Carrega variáveis do ficheiro .env antes dos restantes imports.

No Windows, blocos de notas gravam muitas vezes `.env.txt`; tentamos os dois nomes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv


def app_runtime_root() -> Path:
    """Pasta do projeto em dev; pasta do .exe quando empacotado (PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def load_dotenv_from_app_dir() -> Path:
    """Procura `.env` ou `.env.txt` na pasta de execução. `override=False`: variáveis do SO prevalecem."""
    root = app_runtime_root()
    for name in (".env", ".env.txt"):
        path = root / name
        if path.is_file():
            load_dotenv(path, override=False)
            break
    return root
