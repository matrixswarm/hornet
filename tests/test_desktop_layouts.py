"""Check generated layout contracts without running generated project code."""
import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hornet.desktop.layouts import LAYOUTS
from hornet.desktop.workspace import Workspace, source_files


class LayoutTests(unittest.TestCase):
    def test_recipes_have_valid_source_and_matching_shadows(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            for index, kind in enumerate(LAYOUTS):
                with self.subTest(kind=kind):
                    project = 'sample-' + str(index)
                    revision = ws.create(project, kind)
                    root = ws.revision(project, revision)
                    blueprint = json.loads((root / '.blueprint').read_text())
                    self.assertEqual(blueprint['layout']['id'], kind)
                    self.assertTrue(blueprint['directory_roles'])
                    self.assertTrue(blueprint['rules'])
                    shadows = json.loads((root / '.hornet-meta/shadows.json').read_text())
                    self.assertEqual(set(shadows), set(source_files(root)))
                    for relative in source_files(root):
                        self.assertTrue((root / '.hornet-meta/shadows' / (relative + '.shadow')).is_file())
                        if relative.endswith('.py'):
                            ast.parse((root / relative).read_text())

    def test_php_namespace_mapping_and_document_root(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            rev = ws.create('php-app', 'php-psr4')
            root = ws.revision('php-app', rev)
            config = json.loads((root / 'composer.json').read_text())
            self.assertEqual(config['autoload']['psr-4'], {'App\\': 'src/'})
            self.assertTrue((root / 'src/Greeting/Interfaces/GreetingInterface.php').is_file())
            self.assertTrue((root / 'src/Greeting/AbstractClasses/AbstractGreeting.php').is_file())
            for path in (root / 'src').rglob('*.php'):
                parts = path.relative_to(root / 'src').parts
                namespace = 'App\\' + '\\'.join(parts[:-1])
                self.assertIn('namespace ' + namespace + ';', path.read_text())
                self.assertIn(path.stem, path.read_text())
            self.assertIn('-t public', (root / 'README.md').read_text())

    def test_unknown_layout_does_not_leave_project(self):
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            with self.assertRaises(ValueError):
                ws.create('invalid', 'not-a-recipe')
            self.assertEqual(ws.list_projects(), [])

    def test_python_package_configuration(self):
        import sys
        if sys.version_info < (3, 11):
            self.skipTest('tomllib requires Python 3.11')
        import tomllib
        with TemporaryDirectory() as tmp:
            ws = Workspace(tmp)
            root = ws.revision('sample-app', ws.create('sample-app', 'python-package'))
            config = tomllib.loads((root / 'pyproject.toml').read_text())
            self.assertEqual(config['tool']['setuptools']['packages']['find']['where'], ['src'])
            self.assertTrue((root / 'src/app/__main__.py').exists())
            self.assertIn('python -m pip install -e .', (root / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()
