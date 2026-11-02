from pathlib import Path
import subprocess, sys
out = r"out\launcher_relaunch.log"
p = r"C:\workspace\aoe2_highlights_2\AoE2Control_1.1.5\AoE2Control.exe"
log = Path(out)
process = subprocess.Popen([p, "--headless", "--timeout-ms", "180000"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
print("pid", process.pid, flush=True)
for line in process.stdout:
    print(line.rstrip(), flush=True)
code = process.wait()
print("exit", code)
