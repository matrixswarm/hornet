# Validation scope

## Public release 0.15.1

This release adds publication metadata, the AGPL-3.0-only license, documentation,
CI, a source distribution and a starter bundle. Every executable source file and
unit test matches the frozen 0.15.0 baseline; the sole implementation edit is the
version string in `hornet/__init__.py`. The VM setup helpers and demo are unchanged.

The GitHub checks exercise Python 3.9 and 3.13 on Windows and Ubuntu. A separate
job builds the wheel and source distribution, validates package metadata, installs
the wheel in a fresh environment and runs the tests outside the source checkout.
Check the actual workflow results for the current commit. These checks use
simulated model and worker responses and do not require a live model or VM.

The signed release's `VALIDATION.json` records the local checks completed for its
exact artifacts. Historical live-model evidence below belongs to 0.15.0. A new
model trial was not needed for packaging-only changes and is not claimed here.

## Earlier 0.15.0 first-user trial

See [validation-0.15.0.json](validation-0.15.0.json). The package's 383-test suite
passed in source environments on Python 3.9 and 3.13, a fresh installed-wheel
environment and an Ubuntu worker on Python 3.10.

The first-user walkthrough was then exercised with a fresh Python 3.13 environment
on the existing Windows host and a newly provisioned Ubuntu 22.04 VM. It covered:

- Offline wheel installation, dependency checks and local diagnostics.
- Passing and failing file contracts, and refusal to overwrite an existing project.
- Worker setup refusal for existing configuration and an incorrect image checksum.
- A real Gemma 12B coding goal: 16 model requests, 5 tool calls, peak input 3,084
  tokens, only `calculator.py` changed, checked values `[-6, 0, 14]`.
- Preservation of the original worker baseline and inspection of the completed goal.
- Refusal to resume an already completed goal, with its checkpoint unchanged.

The initial report harness incorrectly expected completed-goal resume to succeed;
that report error was corrected and the refusal rechecked. Separate earlier Gemma
and Qwen trials exercised durable questions and explicit answer/resume behavior.

## Limits

One small live coding goal does not establish general model reliability. Generated
contracts and reviewers can be wrong. Unknown operations stop for inspection;
resume is not a guarantee against every power-loss or external-effect scenario.
The dedicated VM is the execution boundary, and goal directories share its account.
Concurrent workers, a conversational interface and universal zero-drift guarantees
are not part of this release. Review exported changes before integration.
