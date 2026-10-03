import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _local_venv_python() -> Path | None:
    candidate_roots = [PROJECT_ROOT, PROJECT_ROOT.parent]

    for root in candidate_roots:
        venv_dir = root / ".venv"
        if not venv_dir.exists():
            continue

        if os.name == "nt":
            candidate = venv_dir / "Scripts" / "python.exe"
        else:
            candidate = venv_dir / "bin" / "python"

        if candidate.exists():
            return candidate

    return None


def _ensure_project_environment():
    venv_python = _local_venv_python()
    if venv_python is not None:
        current_python = Path(sys.executable).resolve()
        if current_python != venv_python.resolve():
            os.execv(
                str(venv_python),
                [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            )


_ensure_project_environment()

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=False)
