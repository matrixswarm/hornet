# Bundled setup dependency

The starter includes the unmodified `pycdlib` 1.14.0 wheel in `dependencies/`.
It creates the cloud-init seed ISO during VM setup. Hornet has no runtime Python
dependencies.

- Package: `pycdlib-1.14.0-py2.py3-none-any.whl`.
- Upstream: https://github.com/clalancette/pycdlib
- Package metadata declares LGPLv2. Its license text is included in the wheel as
  `pycdlib-1.14.0.dist-info/COPYING`; the wheel contains the Python source.
- The starter's `CONTENTS.json` records the bundled wheel's exact digest and size.

Ubuntu images, VirtualBox, Python and model weights are installed separately and
are not included in the release archives.
