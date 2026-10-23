# Create your first coding worker

This walkthrough creates a new disposable Ubuntu VM containing the starter
exercise. Your model continues to run in LM Studio on the host. Install VirtualBox
and an OpenSSH client first. Run from the extracted starter folder after step 1 of
`START-HERE.md`. No administrator PowerShell or script-policy change is needed.

## Download and check Ubuntu

Use the Ubuntu 22.04 AMD64 **cloud VMDK** from
https://cloud-images.ubuntu.com/jammy/current/ . Download its `SHA256SUMS` as well.
Use the full checksum for the exact VMDK you downloaded; verify Ubuntu's signed
checksum file when establishing trust in a newly downloaded image.

The first-user validation uses the retained 2026-10-20 Ubuntu cloud image with
SHA-256 `eac978cac67cc40afe3785c4d78c97c77a36b16b4db57ae893a69f62de7736b2`.
Newer images have different checksums and are not covered by that validation.
The image must contain Python 3, Git and OpenSSH server; the setup does not run
package upgrades or silently install guest packages. The image is not bundled.

## Create the VM

`pycdlib` is a setup-only dependency used to create the cloud-init seed ISO.
Hornet itself has no runtime Python dependencies.

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --no-deps --find-links .\dependencies pycdlib==1.14.0
$image = 'C:\Downloads\jammy-server-cloudimg-amd64.vmdk'
$imageHash = 'PASTE_THE_FULL_SHA256_FOR_THAT_IMAGE'
$vmFolder = Join-Path $env:USERPROFILE 'VirtualBox VMs'
.\.venv\Scripts\python.exe .\tools\prepare_worker.py --project .\my-first-goal --image $image --image-sha256 $imageHash --vm-parent $vmFolder --name hornet-first-goal --port 2224
```

Choose an unused VM name and loopback port. The helper checks the image hash and
refuses existing VM names, private-key directories and worker configurations.
It generates fresh keys, restricts the local private directory to your Windows
account, and binds the SSH forwarding port to `127.0.0.1`. The VM uses NAT with
outbound networking. Shared folders, clipboard and drag-and-drop are not enabled.

### Linux host

The starter kit's `tools/start_linux.py --download-image` performs the complete
Linux setup, including image download and checksum verification. See the Linux
Linux section in START-HERE.md. The following commands are the manual option.

Run these commands on the Linux host that runs VirtualBox and LM Studio.
Use a fresh extracted starter folder and the project created by `first_run.py`.
Set `image` and `image_hash` to the cloud VMDK and its exact published checksum:

```bash
.venv/bin/python -m pip install --no-index --no-deps --find-links ./dependencies pycdlib==1.14.0
image="$HOME/Downloads/jammy-server-cloudimg-amd64.vmdk"
image_hash='PASTE_THE_FULL_SHA256_FOR_THAT_IMAGE'
.venv/bin/python tools/prepare_worker.py --project ./my-first-goal --image "$image" --image-sha256 "$image_hash" --vm-parent "$HOME/VirtualBox VMs" --name hornet-linux-first --port 2224
.venv/bin/hornet --root ./my-first-goal worker-status --worker-config worker.json --json
```

The helper finds `VBoxManage` and `ssh-keygen` on PATH. It creates the private
directory with mode 0700 and its files with mode 0600. Use another unused VM
name and port if needed. It never adopts, resets or deletes an existing VM.

Do not handcraft `worker.json`, copy an existing Hermes worker, change workspace
paths, or create readiness markers manually. The generated guest uses the
`hornet` account, matching root-owned worker code, an initialized baseline Git
repository and a forced SSH entry point. A successful preflight verifies those
parts together before any model turn. A failure reports the checks that failed.

The guest account `hornet` has no sudo access. Its SSH key accepts the fixed
worker entry point. Agent Python still runs as that guest account; the dedicated
VM is the isolation boundary. Do not place unrelated secrets inside it.

The first boot usually takes a few minutes. Retry this status command until it
reports `ready: true`; an early connection refusal just means it is not ready:

```powershell
.\.venv\Scripts\hornet.exe --root .\my-first-goal worker-status --worker-config worker.json --json
```

If startup fails, inspect the VM console and cloud-init output. Keep the failed
setup for diagnosis. The helper intentionally refuses to overwrite a partial
setup; use another project/name after identifying the failure. Do not rerun it
against an established worker to upgrade it.

## Pause the machine after work

Once the controller has exited, request guest shutdown:

```powershell
& 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe' controlvm hornet-first-goal acpipowerbutton
```

Restart the same VM for resume or revision:

```powershell
& 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe' startvm hornet-first-goal --type headless
```

Keep the project and VM together. `my-first-goal/private/` contains the new SSH
identity, host key and seed ISO; exclude this directory from uploads and Git.
The starter ZIP contains only public material and generates those secrets locally.
