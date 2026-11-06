# Project layout recipes

In **New project**, enter a name and choose a starter recipe. A recipe creates
source files and records directory responsibilities, dependency direction and
conventions in `.blueprint`. Each created file receives a structural `.shadow`.
Import preserves the existing layout rather than forcing it into a recipe.
Layout recipes are distinct from the older CLI's executable task recipes.

## Recommended default: Python src package

```text
project/
  pyproject.toml
  README.md
  src/app/
    __init__.py
    __main__.py
    main.py
    services/
      __init__.py
      greeting.py
  tests/test_greeting.py
  docs/ARCHITECTURE.md
```

The import package is `app`, not `src`. Install the project into a Python
environment before running it; setup and test commands are in its README.
The desktop variant adds `src/app/ui/window.py`. UI can call services;
services must not import UI. Add domain/ for real business rules and
infrastructure/ for external adapters when the project needs them.

For a tiny utility choose **Python · small script** instead. Starting with
empty layers and numerous one-line files makes small missions harder to follow.

Python policy: use lowercase module names, CapWords classes and cohesive modules.
A substantial independent class can have its own file; several tightly related
classes may share a module. These are Hornet recipe choices, not a Python rule
requiring one class per file. Put a component's interfaces/protocols and abstract bases in its own
`interfaces/` and `abstract_classes/` directories when needed.
Prefer clear dependencies over inheritance introduced just to satisfy a layout.

Sources: [Python naming conventions](https://peps.python.org/pep-0008/),
[src and flat layouts](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/).

## PHP PSR-4 foundation

```text
project/
  composer.json
  public/index.php
  src/Greeting/
    Greeting.php
    Interfaces/GreetingInterface.php
    AbstractClasses/AbstractGreeting.php
  tests/greeting.php
  docs/ARCHITECTURE.md
  README.md
```

Composer maps `App\` to `src/`. For example,
`App\Greeting\Greeting` maps to `src/Greeting/Greeting.php`.
Our recipe puts one autoloaded class/interface/trait in each matching file.
PSR-4 specifies namespace-to-path mapping and case sensitivity; it does not
prescribe controllers, service layers or a directory named Abstract.

Each component owns its interfaces in `Interfaces/` and abstract classes in
`AbstractClasses/`, for example `src/Greeting/AbstractClasses/AbstractGreeting.php`. Only `public/` should be the web document root.

This starter requires PHP 8.2+ and Composer in the execution environment. It does
not install them or install dependencies automatically. PHP execution has not
been validated on this host, which has no PHP executable.

Sources: [PSR-4](https://www.php-fig.org/psr/psr-4/),
[Laminas application structure](https://docs.laminas.dev/laminas-mvc/intro/).

## Zend/Laminas-style MVC

A framework-specific recipe would use its official skeleton rather than label
a hand-written PHP starter as framework-ready. Conventional Laminas structure:

```text
config/
module/Application/
  config/
  src/Controller/
  view/
public/index.php
composer.json
```

The current catalog does not include a full Laminas installation. Its official
MVC skeleton reports security-only maintenance, so it is appropriate to evaluate
its lifecycle before selecting it for a new framework-based project.
[Official skeleton status](https://github.com/laminas/laminas-mvc-skeleton).

## Static web

`index.html`, `assets/css/main.css` and `assets/js/main.js` keep content,
presentation and browser behavior separate. There is no backend, framework,
package installation or build system in this recipe.

## Present limits

Hornet reads the selected revision blueprint for each mission and instructs the
model to follow its architecture. This is model guidance, not a deterministic
architecture checker; review the proposed changes before applying them. Shadows are structural scans, and PHP semantic parsing is not
implemented. Missions modify selected files or create exact paths specified in advance;
requesting arbitrary unscoped files is not supported.
Recipes do not reorganize old revisions and do not execute generated code.
