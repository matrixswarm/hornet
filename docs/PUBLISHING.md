# Publishing the signed public beta

The `Publish signed Hornet release` workflow uploads the exact wheel and source
distribution from the signed GitHub release named by its verifier pins. It does
not rebuild them.
The release signature, manifest, package hashes and source commit are pinned in
`scripts/publish_release.py`. A new version requires reviewed updates to those pins.

## Prepare a release

Choose the version and update the package version, changelog and matching
starter references before freezing the source commit. Run the applicable tests,
build the wheel and source distribution from that commit, and inspect both
artifacts. Record only checks actually performed in `VALIDATION.json`, including
the release `version` and exact `source_commit`. The index verification below
checks CLI installation and the first-goal file contract; it does not exercise
the Coder interface, a live model or a VM.

If shipping a matching starter ZIP, assemble it in a clean staging directory
from that source commit. Include the release wheel, `starter/` guides, examples,
tools and worker at the paths expected by the guides, the pinned
`pycdlib-1.14.0-py2.py3-none-any.whl` in `dependencies/`, matching public source
under `source/hornet_cli/`, and release notes and validation evidence. Follow
`starter/THIRD-PARTY.md` for the dependency. The Linux starter checks
`CONTENTS.json`: it must map each bundled file path to an object containing
its SHA-256 and byte count; the manifest itself is not self-hashed. Inspect the
archive and test it in a fresh environment. This repository does not provide an
automated starter ZIP builder. Do not include local environments, project data,
credentials, VM images or private signing material.

Create `SHA256SUMS` from the exact final release assets, including the wheel,
source distribution, `VALIDATION.json` and starter ZIP if provided. The verifier
expects lowercase SHA-256, two spaces and a plain filename on each line. Sign the
manifest with the authorized release signing key and verify the detached
`SHA256SUMS.asc` against the published public key and expected fingerprints.
Attach the signed manifest, validation record and listed assets to the new
immutable GitHub release. The workflow does not build or sign artifacts.

After the release assets exist, update the verifier's version, source commit,
manifest hash, package filenames and package hashes on `main` as a separate
workflow change. Keep the signing identity pins unless an authorized key
rotation is intended. Never replace signed assets or move an existing tag.

## Account setup

Configure a pending GitHub Trusted Publisher in each index account:

| Field | TestPyPI | PyPI |
| --- | --- | --- |
| Project name | `matrixswarm-hornet` | `matrixswarm-hornet` |
| Owner | `matrixswarm` | `matrixswarm` |
| Repository | `hornet` | `hornet` |
| Workflow filename | `publish.yml` | `publish.yml` |
| Environment | `testpypi` | `pypi` |

TestPyPI and PyPI have separate accounts. Complete sign-in and two-factor steps
on their websites. No account password or API token belongs in the repository.
The matching GitHub environments restrict deployment to the `main` branch.

See the official guides for [pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and [TestPyPI](https://packaging.python.org/en/latest/guides/using-testpypi/).

## Publication order

1. Run the workflow from `main`, targeting `testpypi`, with `dry_run` enabled.
2. Run it again targeting `testpypi`, with `dry_run` disabled.
3. Wait for the index download, fresh pip install and first-goal checks to pass.
4. Run it targeting `pypi`, with `dry_run` disabled. Preparation repeats the
   TestPyPI download and installation checks before production publication.
5. Confirm the production verification job passes and inspect the project page.

Only the upload job receives an OIDC permission. Index downloads must match the
signed package hashes. Installation uses an isolated environment, a single
index, the exact wheel hash and no dependency downloads. Failed uploads are not
silently skipped; inspect any partial publication before retrying.

After a new upload, the index may briefly return HTTP 404 for the release's JSON
metadata. Verification retries that lookup up to 13 times, waiting 10 seconds
between attempts. Other HTTP errors, invalid metadata and integrity failures
still fail immediately. If verification fails after a successful upload, inspect
its logs and rerun only the failed verification job once the issue is resolved.

## Verified 0.16.0 publication

On October 23, 2026, the signed 0.16.0 wheel and source distribution were
published through the trusted GitHub publisher. The dry run verified the
release signature and package hashes. TestPyPI then accepted both files and
passed a fresh installation and first-goal check. The production workflow
rechecked TestPyPI before publishing the same signed files to PyPI, where its
fresh installation and first-goal check also passed.

- [Signed GitHub release](https://github.com/matrixswarm/hornet/releases/tag/v0.16.0)
- [Publishing dry run](https://github.com/matrixswarm/hornet/actions/runs/39907813430)
- [TestPyPI publication and verification](https://github.com/matrixswarm/hornet/actions/runs/39907908865)
- [PyPI publication and verification](https://github.com/matrixswarm/hornet/actions/runs/39908076188)
- [Installable PyPI release](https://pypi.org/project/matrixswarm-hornet/0.16.0/)

Both indexes list the exact wheel SHA-256
`823f464da273fa5dc930a8dc209523a7724f78ccb8a681a8bb3763fb26a022bc`
and source distribution SHA-256
`a5dd7b071227c354fa42d0135744d89ddcc066f943d6d37dd75bfa5b5d76b6a0`
from the signed GitHub release. The workflow did not rerun the separate live
model trial described in [the validation scope](VALIDATION.md).

## Verified 0.15.1 publication

On October 22, 2026, the signed wheel and source distribution were published to
both indexes. Hash checks, fresh installation, CLI version and first-goal file
contract checks passed in GitHub's Ubuntu runner and separate fresh Windows
Python 3.13 environments:

- [TestPyPI publication and verification](https://github.com/matrixswarm/hornet/actions/runs/39765058601)
- [PyPI publication and verification](https://github.com/matrixswarm/hornet/actions/runs/39765482447)
- [Installable PyPI release](https://pypi.org/project/matrixswarm-hornet/0.15.1/)

Both first verification attempts encountered a temporary metadata 404 after
successful uploads. Verification passed when rerun after the metadata appeared.
Those failures remain in the workflow history; the bounded retry above was
added afterward. These installation checks did not run a new live-model trial.

The signing key authenticates the GitHub release bytes. PyPI's publisher
attestations identify the GitHub workflow that uploaded those same bytes.

Do not overwrite a signed release or move an existing tag. The publishing
workflow is maintained on `main` independently of the frozen package source
commit.
