"""Build-machine probe: which test makes the pytest process end with a non-zero exit code?"""
import re
import subprocess
import sys

names = re.findall(r"^def (test_\w+)", open("tests/test_gui.py").read(), re.M)
runs = [["tests/test_engine.py", "tests/test_formats.py", "tests/test_readers.py"], ["tests/test_gui.py"]]
runs += [["tests/test_gui.py", "-k", n] for n in names]
for args in runs:
    r = subprocess.run([sys.executable, "-X", "faulthandler", "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
                       capture_output=True, text=True)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    print(f"exit {r.returncode}  {' '.join(args)[-70:]}  | {tail} | {r.stderr.strip()[-200:]!r}", flush=True)
