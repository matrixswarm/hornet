"""Parse changed source without importing it or invoking project commands."""
import json
from pathlib import PurePosixPath

from .workspace import read_source


def check_changed_files(root, base_hashes, source_hashes):
    """Report coverage explicitly; unsupported files are never marked passed."""
    results = []
    for name, digest in sorted(source_hashes.items()):
        if base_hashes.get(name) == digest:
            continue
        suffix = PurePosixPath(name).suffix.lower()
        result = {'path': name, 'source_sha256': digest}
        if suffix not in {'.py', '.pyw', '.json'}:
            results.append(dict(result, status='skipped', reason='No built-in syntax checker for this file type.'))
            continue
        try:
            source = read_source(root, name)
            if suffix == '.json':
                json.loads(source)
            else:
                # Compile only: no imports, execution, bytecode files, or plugins.
                compile(source, name, 'exec', dont_inherit=True)
            result.update(status='passed', checker='JSON parser' if suffix == '.json' else 'Python compiler')
        except (SyntaxError, ValueError, RecursionError) as exc:
            result.update(status='failed', error=str(exc))
        results.append(result)
    return {'scope': 'created or changed files', 'files': results,
            'source_sha256': dict(source_hashes),
            'passed': not any(item['status'] == 'failed' for item in results),
            'note': 'Syntax only. Skipped files are not validated. The user must check functionality.'}


def summary(report):
    if not report:
        return 'No syntax report recorded (older mission).'
    counts = {status: sum(item['status'] == status for item in report['files'])
              for status in ('passed', 'failed', 'skipped')}
    return ('Syntax: {passed} passed, {failed} failed, {skipped} unsupported/skipped. '
            'Functionality needs your review.').format(**counts)
