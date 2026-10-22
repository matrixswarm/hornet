# Contributing

Use a small issue or pull request with one concrete behavior change. Preserve
failed-attempt evidence, frozen contracts and explicit recovery states.

## Local checks

From the repository root, with Python 3.9 or later:

```console
python -m unittest discover -s tests -v
```

The unit suite uses temporary projects and simulated model/worker responses.
It does not require LM Studio, a GPU, a running VM or production credentials.
CI runs the suite on Windows and Ubuntu with Python 3.9 and 3.13, then installs
the built wheel in a separate environment and repeats it outside the checkout.
Live model trials are separate; do not describe unit tests as live model validation.

## Build

In a dedicated virtual environment:

```console
python -m pip install build twine
python -m build
python -m twine check dist/*
```

The package contains `hornet/` and its bundled recipe. Tests, starter instructions
and setup tools are included in the source distribution. Models, VM images and
credentials are never bundled. Keep local runs outside this checkout.

Include your test results and known limits with a pull request. Do not commit
private keys, provider tokens, worker configurations, VM seeds or conversation logs.
