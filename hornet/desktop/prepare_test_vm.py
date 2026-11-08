"""Bind an existing pinned worker and an optional project template; no provisioning."""
import argparse
import json
from pathlib import Path
from hornet.coding.transport import SSHWorker
from .workspace import Workspace, write_json
from .vm_project import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--worker-config', required=True, type=Path)
    parser.add_argument('--project')
    parser.add_argument('--template', type=Path)
    parser.add_argument('--template-path', action='append', default=[])
    parser.add_argument('--revision-path', action='append', default=[])
    args = parser.parse_args()
    config = args.worker_config.resolve(strict=True)
    SSHWorker(config)  # Validate existing connection files, not create credentials.
    ws = Workspace(args.workspace)
    global_path = ws.root / 'test-vm.json'
    if global_path.exists():
        current = json.loads(global_path.read_text(encoding='utf-8'))
        if current != {'worker_config': str(config)}:
            parser.error('A different VM configuration already exists; inspect it before replacing it.')
    profile = None
    if args.project or args.template:
        if not args.project or not args.template or not args.template_path or not args.revision_path:
            parser.error('A template needs project, template-path and revision-path options.')
        project = ws.project(args.project)
        if not project.is_dir():
            parser.error('Choose an existing Hornet project.')
        profile = project / 'test-vm.json'
        if profile.exists():
            parser.error('Project VM settings already exist; inspect them before replacing them.')
        template = args.template.resolve(strict=True)
        if not template.is_dir():
            parser.error('Template must be a directory.')
        from .workspace import safe_path
        for name in args.template_path:
            if not safe_path(template, name).exists():
                parser.error('Missing template path: ' + name)
        for name in args.revision_path:
            safe_path(template, name)
        from pathlib import PurePosixPath
        args.template_path = [PurePosixPath(value).as_posix() for value in args.template_path]
        args.revision_path = [PurePosixPath(value).as_posix() for value in args.revision_path]
        write_json(profile, {'template_directory': str(template), 'template_paths': args.template_path, 'revision_paths': args.revision_path})
        settings(ws, args.project)
    if not global_path.exists():
        write_json(global_path, {'worker_config': str(config)})
    print('Configured the existing VM worker. No credentials, VM settings or source files changed.')


if __name__ == '__main__':
    main()
