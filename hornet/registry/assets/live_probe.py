import subprocess
import sys
from ._common import execute_guarded

@execute_guarded
def execute(args):
    if ("expression" in args) == ("code" in args):
        raise ValueError("Provide exactly one of expression or code")
    code = args["code"] if "code" in args else "print(" + args["expression"] + ")"
    timeout = 15 if "code" in args else 5
    result = subprocess.run([sys.executable, "-c", code],
                            capture_output=True, text=True, timeout=timeout, errors="replace")
    return {"status": "SUCCESS" if result.returncode == 0 else "ERROR",
            "stdout": result.stdout[:32000], "stderr": result.stderr[:32000], "exit_code": result.returncode}
