# Getting started with Hornet Coder

Hornet Coder opens Mission Control for code projects and reviewable model
missions. This guide uses a local LM Studio server and does not require a VM
unless you choose to configure the optional VM check runner.

## 1. Install and start

Install Git, Python 3.9 or newer and Tk. On Ubuntu or Debian, install
`python3-venv` and `python3-tk` as well. In a terminal:

```sh
git clone https://github.com/matrixswarm/hornet.git
cd hornet
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/hornet-coder
```

On Windows, use PowerShell in the cloned repository:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\hornet-coder.exe
```

Start the LM Studio server and load a model before using model features. The
usual local server address is `http://localhost:1234/v1`; enter your server
address if different. Files and prompts sent to that server are visible to it.

## 2. Try Mission Control

1. Choose **New project** or **Import folder**. When importing, review the file
   selection carefully; the defaults are only a precaution. Your original
   folder is left alone.
2. Select a revision and a model, then choose **Initialize** under Project setup.
   Check the source files and blueprint that will be sent to the model. Wait for the
   project map, review it, and choose **Accept map**. If initialization
   stops early, select **Initialize** again to resume it.
3. Select the accepted revision, enter a small mission in the mission box, and
   select **Run mission**. The Activity tab shows progress.
4. Read the Proposal, including source changes and checks. An empty optional VM
   check field means checks were skipped; run the appropriate tests yourself.
   Choose **Accept mission** only after reviewing the result.

Each revision records its parent. The colored dots and short IDs help you see
that relationship. Right-click a revision to name it, move an eligible revision
to recoverable trash, or **Save revision to disk**. See
[Mission Control details](DESKTOP.md) for initialization and recovery.

## 3. Back up and move to another computer

The source repository and project data are separate. Mission Control projects
are under `~/.hornet/projects` by default and can contain private source files,
prompts and model outputs. Back up the data you intend to transfer and recreate
the virtual environment on the new computer using the installation steps above.
