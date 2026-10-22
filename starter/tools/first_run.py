"""Exercise the installed CLI in a new project, without a model or worker."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    args = parser.parse_args()
    target = args.project.resolve()
    if target.exists():
        parser.error('Project already exists; choose a new directory to preserve its contents')
    kit = Path(__file__).resolve().parent.parent

    def cli(*arguments, expected=0):
        result = subprocess.run([sys.executable, '-I', '-m', 'hornet', *map(str, arguments)],
                                capture_output=True, text=True, encoding='utf-8')
        if result.returncode != expected:
            raise RuntimeError(result.stdout + result.stderr)
        print(result.stdout.strip())
        return result.stdout

    cli('init', '--name', 'my-first-goal', '--path', target)
    cli('--root', target, 'doctor', '--json')
    (target / 'hello.txt').write_text('Hornet is ready.\n', encoding='utf-8')
    contract = {'target_files': {'hello.txt': {'exists': True, 'contains': ['Hornet is ready'], 'min_size': 10}}}
    (target / 'contract.json').write_text(json.dumps(contract, indent=2), encoding='utf-8')
    cli('--root', target, 'verify', '--contract', 'contract.json', '--json')
    shutil.copyfile(kit / 'examples/demo-goal.txt', target / 'demo-goal.txt')
    print('INSTALLATION CHECK PASSED')
    print('Project: ' + str(target))
    print('Next: WORKER-SETUP.md, then the coding goal in START-HERE.md.')


if __name__ == '__main__':
    main()
