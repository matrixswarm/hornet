# Verify a MatrixSwarm Hornet release

Keep `SHA256SUMS`, `SHA256SUMS.asc`, the ZIP/wheel, and the public key together.
Use GnuPG (Gpg4win on Windows). The detached OpenPGP signature covers the exact
checksum manifest; the manifest binds the release files to their SHA-256 hashes.

MatrixSwarm primary fingerprint:

```text
8086 C2A9 440B 9226 FA7A 78E5 BC01 80CF 9A93 5966
```

Release signing subkey fingerprint:

```text
42FE F65A A81A D656 7DEA 2091 75EB 12C3 FF29 3394
```

Compare the primary fingerprint with a trusted, independently obtained
MatrixSwarm release identity before trusting this download. A repository copy
is available in `signing/matrixswarm-release-signing-key.fingerprint`:
https://github.com/matrixswarm/hornet/tree/main/signing .
That copy helps check consistency. A key and checksum downloaded from the same
place alone do not establish publisher identity independently.

## Inspect the key, then verify the signature

```powershell
gpg --show-keys --with-fingerprint .\matrixswarm-release-signing-key.asc
```

After matching the full primary fingerprint above to your trusted copy:

```powershell
gpg --import .\matrixswarm-release-signing-key.asc
if ($LASTEXITCODE -ne 0) { throw 'Public key import failed' }
gpg --status-fd 1 --verify .\SHA256SUMS.asc .\SHA256SUMS
if ($LASTEXITCODE -ne 0) { throw 'Release signature failed' }
```

Look for `VALIDSIG` with the signing fingerprint above and the expected primary
fingerprint. A trust-level warning is different from an invalid signature:
the fingerprint comparison establishes which publisher you intend to trust.
Do not continue after a bad/missing signature or a different signing identity.

## Check the release bytes

In PowerShell, after verifying the manifest's signature:

```powershell
Get-Content -LiteralPath .\SHA256SUMS | ForEach-Object {
    if ($_ -notmatch '^([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)$') { throw 'Invalid manifest line' }
    $expectedHash = $Matches[1]
    $releaseFile = $Matches[2]
    $actualHash = (Get-FileHash -LiteralPath (Join-Path $PWD $releaseFile) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) { throw "Checksum mismatch: $releaseFile" }
    Write-Host "Verified: $releaseFile"
}
```

On Linux, run `sha256sum --check SHA256SUMS` after the same GnuPG verification.
Extract the verified starter ZIP into a new directory and open `START-HERE.md`.
This signature authenticates the signed release bytes; it is not a Bitcoin
timestamp or a claim that all possible behaviors have been tested.

GnuPG's verification reference:
https://www.gnupg.org/documentation/manuals/gnupg/GPG-Examples.html .
