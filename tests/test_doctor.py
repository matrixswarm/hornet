"""Local doctor behavior, exit codes, and read-only operation."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hornet.cli import main
from hornet.doctor import doctor
from hornet.operations import initialize
from hornet.project import Project


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hornet-doctor-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        initialize('doctor-test', self.root)

    def cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['--root', str(self.root), 'doctor', *args])
        return code, output.getvalue()

    def test_initialized_project_passes_json_and_writes_nothing(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('urllib.request.build_opener', side_effect=AssertionError('No network')):
            code, text = self.cli('--json')
        result = json.loads(text)
        self.assertEqual(code, 0)
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['checks']), 4)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_missing_directory_is_failure_exit_one(self):
        (self.root / 'continue/pointers').rmdir()
        code, text = self.cli('--json')
        self.assertEqual(code, 1)
        item = json.loads(text)['checks'][2]
        self.assertFalse(item['ok'])
        self.assertEqual(item['detail'], 'Missing')

    def test_file_instead_of_directory_is_failure(self):
        (self.root / 'continue/goals').rmdir()
        (self.root / 'continue/goals').write_text('preserve', encoding='utf-8')
        result = doctor(Project(self.root))
        self.assertFalse(result['ok'])
        self.assertIn('not a directory', result['checks'][1]['detail'])
        self.assertEqual((self.root / 'continue/goals').read_text(), 'preserve')

    def test_versions_use_major_minor_tuple(self):
        for version, expected in (((3, 8, 0), False), ((3, 9, 0), True), ((3, 13, 0), True), ((4, 0, 0), True)):
            with self.subTest(version=version), patch('hornet.doctor.sys.version_info', version):
                self.assertEqual(doctor(Project(self.root))['checks'][0]['ok'], expected)

    def test_text_has_each_check_name_and_detail(self):
        code, text = self.cli()
        self.assertEqual(code, 0)
        for item in doctor(Project(self.root))['checks']:
            self.assertIn('PASS', text)
            self.assertIn(item['name'], text)
            self.assertIn(item['detail'], text)

    def test_common_root_flag_after_command(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['doctor', '--root', str(self.root), '--json'])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])


if __name__ == '__main__':
    unittest.main()
