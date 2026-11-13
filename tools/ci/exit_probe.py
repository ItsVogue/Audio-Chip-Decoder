"""Build-machine probe: why does the full pytest run end with a non-zero exit code?"""
import subprocess
import sys

import pytest

print("pytest", pytest.__version__, flush=True)
runs = [["tests"], ["-p", "no:cacheprovider", "tests"], ["tests/test_gui.py", "tests/test_readers.py"],
        ["tests/test_formats.py", "tests/test_gui.py"], ["tests/test_engine.py", "tests/test_gui.py"],
        ["tests/test_gui.py", "tests/test_jaws.py"]]
for args in runs:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", *args], capture_output=True, text=True)
    print(f"exit {r.returncode}  {' '.join(args)}", flush=True)
    if r.returncode:
        print("  stdout tail:", r.stdout.strip().splitlines()[-6:], flush=True)
        print("  stderr tail:", r.stderr.strip()[-1500:], flush=True)
