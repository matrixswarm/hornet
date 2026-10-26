import subprocess
from ._common import execute_guarded

@execute_guarded
def execute(args):
    result = subprocess.run(args["command"], shell=True, capture_output=True, text=True,
                            timeout=args.get("timeout", 30), cwd=args.get("cwd"), errors="replace")
    limit = 32000
    return {"status": "SUCCESS" if result.returncode == 0 else "ERROR",
            "stdout": result.stdout[:limit], "stderr": result.stderr[:limit], "exit_code": result.returncode,
            "truncated": len(result.stdout) > limit or len(result.stderr) > limit}
