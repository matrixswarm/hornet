# Getting started with Hornet

Hornet has two desktop applications. **Coder** works with code projects and
reviewable missions. **Distil** maintains a chat and turns it into reusable
context. This walkthrough shows how to start both and try each one with a local
model server.

## 1. Install and launch

Install Git, Python 3.9 or newer, and Tk. On Ubuntu or Debian, install
`python3-venv` and `python3-tk` as well. From a terminal:

```sh
git clone https://github.com/matrixswarm/hornet.git
cd hornet
python3 -m venv .venv
.venv/bin/python -m pip install '.[distil]'
.venv/bin/hornet-launcher
```

On Windows, use PowerShell in the cloned repository:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[distil]"
.\.venv\Scripts\hornet-launcher.exe
```

Choose **Open Coder** or **Open Distil**. The `distil` extra installs the Qt
and HTTP dependencies needed by Distil. Both applications use the same Python
**environment** as the launcher, but keep separate project data. You can also
launch either directly using `hornet-coder` or `hornet-distil` from the virtual
environment. From a checkout, `python -m hornet.launcher` opens the chooser
when run with an environment containing the dependencies.

Start your OpenAI-compatible server, such as LM Studio, and load a model before
using model features. The usual local URL is `http://127.0.0.1:1234`; use the
server's actual address if it runs elsewhere. Coder uses the `/v1` endpoint.
Selected project files, prompts and active chat sent to a model are visible to
that server. A suitable model must support the structured responses Coder
requests; models differ in speed and reliability.

## 2. Try a Coder mission

1. Choose **Open Coder**, then **+ New → Calculator example**. Give the
   project a name. This creates a working calculator and tests as an ordinary
   project in your Hornet workspace. Alternatively choose **Import folder**;
   review the selected files before importing. Hornet copies them and leaves
   the original folder alone.
2. Select the new project's revision. Open **Model settings**, enter the
   server URL ending in `/v1`, use **Find models**, and choose a loaded model.
3. Choose **Understand project**. Review the files being sent, wait for the
   project understanding, and review it. Use **Correct understanding** if
   needed, then **Looks right, continue** to accept it. If understanding is
   interrupted, resume it before continuing.
4. Select the accepted revision and choose **Project actions → Create working
   revision**. In **What should change?**, try: “Add a subtract function to
   the calculator and tests for it.” Choose **Run mission**.
5. Review the proposed plan and its file scope. Choose **Approve plan &
   continue** to let Coder work, or **Counter plan** to request a revision.
   Watch **Activity** for progress. Once a candidate is ready, review its
   changes and receipts, then choose **Accept reviewed changes** only if you
   want to keep it.

Coder checks edit structure and syntax, but those checks do not establish that
code behaves correctly. Run appropriate tests yourself. The optional Test VM
runner is not configured automatically; an empty VM check does not mean a
functional test passed. See [the desktop guide](DESKTOP.md) for VM setup,
recovery, revision controls and safety details.

## 3. Try Distil

1. Choose **Open Distil** in the launcher. Select **+ New project**, name it,
   then select **+ New revision** and name the revision.
2. Open **Model settings**. Check the server URL and choose a model from the
   list. Distil requests the list in the background without starting inference;
   use **Refresh models** or type a model identifier if needed. Distil does
   not automatically select a model for you.
3. Enter a message and press **Send** or Enter. Shift+Enter inserts a newline.
   Continue the conversation, then choose a detail level and press **Distill**.
   Send or clear any unsent draft first.
4. After a complete result saves successfully, it becomes the active context
   for later messages. Use **Save revision…** to export the selected revision
   as PDF, text or Markdown. You can also use **Clone** to make an independent
   copy, or **Import distilled text…** to start from an existing text file.

The active conversation is sent to the configured server when you send or
distill. Review the output before relying on it. See the [Distil guide](DISTIL.md)
for model settings, storage, exports and recovery.

## 4. Back up your work

The application source and project data are separate. By default Coder stores
projects under `~/.hornet/projects`; Distil stores its database and revision
files under `~/hornet_distil`. Back up the data you need while the applications
are closed. Coder can also use a different projects folder, and Distil supports
`HORNET_DISTIL_ROOT`; check your configured locations before moving computers.
Recreate the Python environment on the new computer using the installation
steps above.
