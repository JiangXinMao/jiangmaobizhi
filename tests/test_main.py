import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_entrypoint_import_does_not_load_heavy_application_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, main; print('jiangmao_wallpaper.app' in sys.modules)",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "False"


def test_duplicate_entrypoint_exits_before_heavy_application_import():
    script = """
import sys
import jiangmao_wallpaper.single_instance as module

class DuplicateGuard:
    def acquire(self): return False
    def request_activation(self): return True
    def activate_existing_window(self): return True

module.SingleInstanceGuard = DuplicateGuard
import main
print(main.main([]))
print('jiangmao_wallpaper.app' in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.splitlines() == ["0", "False"]
