import subprocess
from ._common import execute_guarded

@execute_guarded
def execute(args):
    selected = args.get("shell")
    if selected == "powershell":
        command, use_shell = ["powershell.exe", "-NoProfile", "-Command", args["command"]], False
    elif selected == "bash":
        command, use_shell = ["bash", "-c", args["command"]], False
    elif selected is None:
        command, use_shell = args["command"], True
    else:
        raise ValueError("Unsupported shell")
    result = subprocess.run(command, shell=use_shell, capture_output=True, text=True,
                            timeout=args.get("timeout", 30), cwd=args.get("cwd"), errors="replace")
    limit = 32000
    return {"status": "SUCCESS" if result.returncode == 0 else "ERROR",
            "stdout": result.stdout[:limit], "stderr": result.stderr[:limit], "exit_code": result.returncode,
            "truncated": len(result.stdout) > limit or len(result.stderr) > limit}
