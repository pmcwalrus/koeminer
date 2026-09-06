"""Run on each target OS: python scripts/build.py."""
import subprocess
import sys

subprocess.run([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
    "--name", "koeminer", "--noupx", "--collect-submodules", "PySide6.QtMultimedia",
    "run.py",
], check=True)
