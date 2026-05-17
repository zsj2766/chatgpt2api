import subprocess, sys, re

for step in range(20):
    result = subprocess.run(
        [sys.executable, "-c", "from api.app import create_app"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("All dependencies satisfied")
        break
    stderr = result.stderr
    m = re.search(r"No module named '(\w+)'", stderr)
    if m:
        mod = m.group(1)
        print(f"[{step}] Installing {mod}...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", mod, "--quiet"], capture_output=True)
        if r.returncode != 0:
            print(f"  FAILED: {r.stderr[-200:]}")
    else:
        print(f"Unknown error:\n{stderr[-500:]}")
        break
