"""Small conventional starter layouts and the architectural rules behind them."""
import json

LAYOUTS = {
    'Python': ('Python · small script', 'A small utility. Keep related functions together; split by responsibility as it grows.'),
    'python-package': ('Python · src package', 'A distributable application/library. src/app contains runtime code; tests and docs stay separate.'),
    'python-desktop': ('Python · desktop app', 'A native Tk app. UI handles presentation; services own behavior; domain contains framework-independent rules.'),
    'Web': ('Web · static site', 'HTML entry point with separate CSS and JavaScript assets. No server framework or build system.'),
    'php-psr4': ('PHP · PSR-4 foundation', 'Composer autoloading with App\\ mapped to src/. This is framework-independent, not a complete Laminas application.'),
    'calculator-example': ('Example · calculator', 'A small working Python calculator with tests, ready for a practice mission.'),
}


def scaffold(kind, name):
    if kind not in LAYOUTS:
        raise ValueError('Unknown project layout: ' + str(kind))
    label, description = LAYOUTS[kind]
    rules = [
        'Organize by feature and responsibility; add directories when they contain useful code.',
        'Keep entry points thin and project dependencies explicit.',
        'Review the blueprint when adding a responsibility or changing a dependency direction.',
    ]
    if kind == 'calculator-example':
        files = {
            'calculator.py': '"""A small calculator for practicing Hornet missions."""\n\n\ndef add(a, b):\n    return a + b\n\n\nif __name__ == "__main__":\n    print("2 + 3 =", add(2, 3))\n',
            'test_calculator.py': 'import unittest\nfrom calculator import add\n\n\nclass CalculatorTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n        self.assertEqual(add(-1, 1), 0)\n\n\nif __name__ == "__main__":\n    unittest.main()\n',
        }
        roles = {'calculator.py': 'Calculator functions and a small command-line demonstration.',
                 'test_calculator.py': 'Checks for calculator behavior.'}
    elif kind == 'Python':
        files = {'main.py': '"""Project entry point."""\n\n\ndef main():\n    print("Ready for your first mission")\n\n\nif __name__ == "__main__":\n    main()\n'}
        roles = {'main.py': 'Initial entry point and small utility behavior.'}
    elif kind in {'python-package', 'python-desktop'}:
        desktop = kind == 'python-desktop'
        files = {
            'src/app/__init__.py': '"""Application package."""\n',
            'src/app/__main__.py': '"""Module entry point."""\nfrom .main import main\n\nif __name__ == "__main__":\n    main()\n',
            'src/app/main.py': ('"""Compose the native application."""\nfrom .ui.window import run\n\n\ndef main():\n    run()\n' if desktop else
                                '"""Application entry point."""\nfrom .services.greeting import greet\n\n\ndef main():\n    print(greet("Commander"))\n'),
            'src/app/services/__init__.py': '"""Application operations."""\n',
            'src/app/services/greeting.py': '"""Produce a greeting without presentation or storage dependencies."""\n\n\ndef greet(name):\n    return f"Hello, {name}!"\n',
            'tests/test_greeting.py': '"""Check the greeting service."""\nimport unittest\nfrom app.services.greeting import greet\n\n\nclass GreetingTests(unittest.TestCase):\n    def test_greeting(self):\n        self.assertEqual(greet("Commander"), "Hello, Commander!")\n',
            'pyproject.toml': '[build-system]\nrequires = ["setuptools>=77"]\nbuild-backend = "setuptools.build_meta"\n\n[project]\nname = "' + name.lower().replace('_', '-') + '"\nversion = "0.1.0"\nrequires-python = ">=3.9"\ndependencies = []\n\n[tool.setuptools.packages.find]\nwhere = ["src"]\n',
        }
        if desktop:
            files.update({
                'src/app/ui/__init__.py': '"""Native presentation layer."""\n',
                'src/app/ui/window.py': '"""Render the native window."""\nimport tkinter as tk\nfrom app.services.greeting import greet\n\n\ndef run():\n    root = tk.Tk()\n    root.title("Hornet project")\n    tk.Label(root, text=greet("Commander"), padx=40, pady=30).pack()\n    root.mainloop()\n',
            })
        roles = {'src/app/': 'Runtime application package.', 'src/app/main.py': 'Composition and entry point.',
                 'src/app/services/': 'Application behavior, independent of the UI.', 'tests/': 'Checks against the installed package.',
                 'pyproject.toml': 'Package metadata and build configuration.', 'docs/': 'Architecture and usage documentation.'}
        if desktop:
            roles['src/app/ui/'] = 'Tk presentation. May call services; services must not import UI.'
        rules += ['Python modules use lowercase snake_case; classes use CapWords.',
                  'Use one class per file by default; exceptions require explicit user instruction or a special-purpose file.',
                  'Each component owns its interfaces and abstract classes; use component-local interfaces/ and abstract_classes/ when needed.',
                  'Use src/app as the import package; src itself is not an import package.',
                  'Dependency direction: entry point/UI -> services -> domain; domain must not import UI.',
                  'Create domain/ and infrastructure/ only when real domain rules or external adapters are introduced.']
    elif kind == 'Web':
        files = {
            'index.html': '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Hornet project</title>\n<link rel="stylesheet" href="assets/css/main.css">\n<script defer src="assets/js/main.js"></script>\n</head>\n<body><main><h1>Ready for your first mission</h1></main></body>\n</html>\n',
            'assets/css/main.css': 'body { font-family: system-ui, sans-serif; margin: 3rem; }\n',
            'assets/js/main.js': '"use strict";\n// Add page behavior here.\n',
        }
        roles = {'index.html': 'Page structure and accessible content.', 'assets/css/': 'Presentation.', 'assets/js/': 'Browser behavior.'}
    else:
        files = {
            'composer.json': json.dumps({'name': 'hornet/' + name.lower().replace('_', '-'), 'type': 'project',
                                        'require': {'php': '>=8.2'}, 'autoload': {'psr-4': {'App\\': 'src/'}}}, indent=2) + '\n',
            'src/Greeting/Interfaces/GreetingInterface.php': '<?php\ndeclare(strict_types=1);\n\nnamespace App\\Greeting\\Interfaces;\n\ninterface GreetingInterface\n{\n    public function greet(string $name): string;\n}\n',
            'src/Greeting/AbstractClasses/AbstractGreeting.php': '<?php\ndeclare(strict_types=1);\n\nnamespace App\\Greeting\\AbstractClasses;\n\nuse App\\Greeting\\Interfaces\\GreetingInterface;\n\nabstract class AbstractGreeting implements GreetingInterface\n{\n}\n',
            'src/Greeting/Greeting.php': '<?php\ndeclare(strict_types=1);\n\nnamespace App\\Greeting;\n\nuse App\\Greeting\\AbstractClasses\\AbstractGreeting;\n\nfinal class Greeting extends AbstractGreeting\n{\n    public function greet(string $name): string\n    {\n        return "Hello, {$name}!";\n    }\n}\n',
            'public/index.php': '<?php\ndeclare(strict_types=1);\n\nrequire dirname(__DIR__) . "/vendor/autoload.php";\n\n$greeting = new App\\Greeting\\Greeting();\nheader("Content-Type: text/plain; charset=utf-8");\necho $greeting->greet("Commander");\n',
            'tests/greeting.php': '<?php\ndeclare(strict_types=1);\nrequire dirname(__DIR__) . "/vendor/autoload.php";\n\nif ((new App\\Greeting\\Greeting())->greet("Commander") !== "Hello, Commander!") {\n    throw new RuntimeException("Greeting check failed");\n}\necho "Greeting check passed\\n";\n',
        }
        roles = {'public/': 'Only web-accessible document root.', 'src/Greeting/': 'Greeting feature namespace.',
                 'src/Greeting/Interfaces/': 'Interfaces owned by the greeting component.',
                 'src/Greeting/AbstractClasses/': 'Abstract classes owned by the greeting component.',
                 'composer.json': 'Dependencies and PSR-4 namespace mapping.', 'tests/': 'Standalone checks.'}
        rules += ['One autoloaded class/interface/trait per matching PHP file; preserve namespace and filename case.',
                  'App\\ maps to src/; namespace subdirectories match exactly.',
                  'Each component owns its interfaces in Interfaces/ and abstract classes in AbstractClasses/; use matching PSR-4 namespaces.',
                  'Only public/ is served; src/, vendor/ and configuration are not the document root.',
                  'This is not a Zend or Laminas framework skeleton.']
    if kind == 'calculator-example':
        commands = 'In the worker VM:\n\n    python calculator.py\n    python -m unittest test_calculator\n\nTry a mission such as adding a subtract function and its tests.\n'
    elif kind in {'python-package', 'python-desktop'}:
        commands = 'In the worker VM:\n\n    python -m pip install -e .\n    python -m app\n    python -m unittest discover -s tests\n'
        if kind == 'python-desktop':
            commands += '\nThe desktop starter requires Tk and a graphical session.\n'
    elif kind == 'php-psr4':
        commands = 'In the worker VM with PHP 8.2+ and Composer:\n\n    composer install\n    php tests/greeting.php\n    php -S 127.0.0.1:8080 -t public\n'
    elif kind == 'Web':
        commands = 'Preview index.html or serve this directory from the worker VM. No build step.\n'
    else:
        commands = 'In the worker VM:\n\n    python main.py\n'
    files['README.md'] = '# ' + name + '\n\n' + description + '\n\n' + commands
    files['docs/ARCHITECTURE.md'] = '# Architecture\n\n' + description + '\n\n' + '\n'.join('- ' + r for r in rules) + '\n'
    blueprint = {'project': name, 'purpose': 'Describe the intended system here.', 'layout': {'id': kind, 'version': 1, 'label': label},
                 'architecture': description, 'directory_roles': roles, 'rules': rules, 'status': 'starter structure; purpose needs human review'}
    return files, blueprint
