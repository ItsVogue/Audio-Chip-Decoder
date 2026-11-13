"""Stands in for minipro in tests: a pretend XGecu programmer holding the bytes in FAKE_MINIPRO_DATA."""
import os
import sys

args = sys.argv[1:]
WRITE_OR_ERASE = {"-w", "--write", "-E", "--erase", "-F", "--update", "-P", "--protect", "-u", "--unprotect",
                  "-t", "--hardware_check"}
if WRITE_OR_ERASE & set(args):
    sys.stderr.write("TEST FAILURE: a write or erase option was passed\n")
    sys.exit(9)
mode = os.environ.get("FAKE_MINIPRO_MODE", "ok")
if "-k" in args:
    sys.stderr.write("[No programmer found]\n" if mode == "none" else os.environ.get("FAKE_MINIPRO_MODEL",
                                                                                     "t48: T48") + "\n")
    sys.exit(0)
if "-p" in args and "-r" in args:
    device, out = args[args.index("-p") + 1], args[args.index("-r") + 1]
    if not os.path.exists("infoic.xml") and os.environ.get("FAKE_MINIPRO_NEED_DB"):
        sys.stderr.write("infoic.xml: No such file or directory\n")
        sys.exit(1)
    if mode == "none":
        sys.stderr.write("No programmer found.\n")
        sys.exit(1)
    sys.stderr.write("Found T48 00.1.33 (0x121)\n")
    if mode == "contact":
        sys.stderr.write("Bad contact on pin:5\nBad contact on pin:28\n")
        sys.exit(1)
    if "-x" in args:
        sys.stderr.write("WARNING: skipping Chip ID test\n")
    elif mode == "id":
        sys.stderr.write("Chip ID mismatch: expected 0x0197, got 0x1C04 (EON EN27C020)\n")
        sys.exit(1)
    data = open(os.environ["FAKE_MINIPRO_DATA"], "rb").read()
    sys.stderr.write("\r\x1b[KReading Code...  ")
    for pct in (3, 25, 50, 75, 99):
        sys.stderr.write("\r\x1b[KReading Code...  %2d%%" % pct)
        sys.stderr.flush()
    sys.stderr.write("\r\x1b[KReading Code...  0.52 Sec  OK\n")
    open(out, "wb").write(data)
    with open(os.environ.get("FAKE_MINIPRO_LOG", os.devnull), "a") as log:
        log.write(" ".join(args) + "\n")
    sys.exit(0)
sys.exit(1)
