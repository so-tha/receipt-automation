"""
Garante que todos os módulos do pacote de aplicação (`src`) importam sem erro.

Uso (na raiz do repositório):
  python scripts/verify_imports.py

Variáveis de ambiente (CI): AUTH_ENABLED=false; no Linux, QT_QPA_PLATFORM=offscreen
se ainda não estiver definido.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
import pkgutil
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _bootstrap_env() -> None:
    os.environ.setdefault("AUTH_ENABLED", "false")
    if sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _ensure_repo_on_path(root: Path) -> None:
    s = str(root)
    if s not in sys.path:
        sys.path.insert(0, s)


def import_all_under_src(repo_root: Path) -> list[str]:
    """Importa recursivamente cada submódulo sob `src/`. Retorna os nomes na ordem usada."""
    src_dir = repo_root / "src"
    if not src_dir.is_dir():
        raise SystemExit("Diretório src/ não encontrado na raiz do repositório.")

    # Descobre módulos pelo filesystem (não exige `import src` antes da lista).
    discovered = [m for _, m, _ in pkgutil.walk_packages([str(src_dir)], "src.")]
    # Ordem estável: camada mais rasa primeiro reduz alguns casos de dependência circular.
    modnames = sorted(discovered, key=lambda n: (n.count("."), n))
    errors: list[tuple[str, Exception]] = []
    for name in modnames:
        try:
            importlib.import_module(name)
        except Exception as e:
            errors.append((name, e))
    if errors:
        for name, err in errors:
            print(f"Falha ao importar {name}: {err}", file=sys.stderr)
        raise SystemExit(1)
    return modnames


def load_main_entrypoint(root: Path) -> None:
    """Carrega main.py como módulo (sem executar __main__), espelhando o bootstrap real."""
    path = root / "main.py"
    spec = importlib.util.spec_from_file_location("_loglife_main_entry", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Não foi possível carregar main.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica imports de src e main.py")
    parser.add_argument(
        "--skip-main",
        action="store_true",
        help="Não carregar main.py (apenas pacote src)",
    )
    args = parser.parse_args()

    root = _repo_root()
    _ensure_repo_on_path(root)
    _bootstrap_env()

    names = import_all_under_src(root)
    if not args.skip_main:
        load_main_entrypoint(root)

    msg = f"OK: {len(names)} módulo(s) importados em src"
    if not args.skip_main:
        msg += " e main.py carregado"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
