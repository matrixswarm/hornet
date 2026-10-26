import subprocess
import sys
from ._common import execute_guarded

@execute_guarded
def execute(args):
    result = subprocess.run([sys.executable, "-c", "print(" + args["expression"] + ")"],
                            capture_output=True, text=True, timeout=5, errors="replace")
    return {"status": "SUCCESS" if result.returncode == 0 else "ERROR",
            "stdout": result.stdout[:32000], "stderr": result.stderr[:32000], "exit_code": result.returncode}
