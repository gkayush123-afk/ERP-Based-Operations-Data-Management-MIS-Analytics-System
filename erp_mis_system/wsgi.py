import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _local_venv_python() -> Path | None:
    venv_dir = PROJECT_ROOT / ".venv"
    if not venv_dir.exists():
        return None

    if os.name == "nt":
        candidate = venv_dir / "Scripts" / "python.exe"
    else:
        candidate = venv_dir / "bin" / "python"

    return candidate if candidate.exists() else None


def _ensure_project_environment():
    try:
        import flask  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    venv_python = _local_venv_python()
    if venv_python is None:
        return

    current_python = Path(sys.executable).resolve()
    if current_python != venv_python.resolve():
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_project_environment()

from app import create_app
from app.config import ProductionConfig

app = create_app(ProductionConfig)
