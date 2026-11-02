from pathlib import Path
import subprocess

launcher = r"C:\workspace\aoe2_highlights_2\AoE2Control_1.1.5\AoE2Control.exe"
args = [
    launcher,
    "--headless",
    "--timeout-ms", "180000",
    "--override-settings", r"C:\workspace\aoe2_highlights_2\tools\aoe2control\settings.villpop.ini",
    "--override-module", r"C:\workspace\aoe2_highlights_2\tools\aoe2control\vill_pop_recorder",
]
process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
for line in process.stdout:
    print(line.rstrip(), flush=True)
print("exit", process.wait())
