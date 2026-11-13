# Hornet Distil

Hornet Distil is an optional local chat workspace with one active conversation
per manually managed project revision. It is separate from Coder's projects.

## Installation and launch

Use Python 3.9 or newer in a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[distil]'
.venv/bin/hornet-distil
```

On Windows, use `py -3 -m venv .venv`, install with
`.venv\Scripts\python.exe -m pip install ".[distil]"`, then run
`.venv\Scripts\hornet-distil.exe`.

You can also run `python -m hornet.distil` with dependencies available.
`start-distil.sh` uses `python3` by default, or the interpreter selected by
`PYTHON`; `start-distil.bat` uses `python` on PATH. Distil requires requests
and PyQt6; a system PyQt5 installation is supported as a fallback. Coder's Tk
requirement is separate.

## The workflow

1. Create a project, then explicitly create its first revision.
2. Optionally **Import distilled text** from a UTF-8 text/Markdown file. Import
   copies its contents into the revision's `distilled.md` and shows it as the
   active chat's initial context. The source file is never modified. JSON files
   are treated as plain text, not parsed as old chat sessions. Importing over an
   existing chat asks before replacing it; old history remains locally retained.
3. Chat with **Send** or Enter. Shift+Enter inserts a newline.
4. Choose **Brief**, **Balanced**, or **Detailed**, then press **Distill**. Send or
   clear any unsent draft first. The full active conversation is sent, including
   the previous distill and every subsequent message in order. Archived history
   before that distill is not sent again.
5. Only after the complete result is saved successfully does it replace the
   active chat, between explicit distilled-context delimiters. Later messages
   append underneath. Another Distill replaces the same revision's one file.

The detail selector guides the model's level of explanation. It is not a hard
word limit or permission to omit important decisions or constraints. Hornet does
not clip input messages, remove old active turns, or silently shorten a reply.
If the full chat is too large for the loaded model, increase its context window
or choose another model. A server that silently truncates prompts on its own
cannot be detected by this client; configure the server to reject overflow.

**Revisions are manual.** New creates an empty revision. Clone creates an
independent copy of the selected revision's active chat, draft, detail setting,
distilled file, and retained history. Rename changes its label. Delete removes
it from the normal list, with confirmation, but retains data; **Restore deleted
revision** brings it back. Distill never creates a new revision automatically.
Selecting a revision restores its saved active chat and draft.

There are no file attachments, separate sessions, autonomous summarization,
external tools, or background model calls.

## Line numbers and saving a revision

The main chat has an always-visible line-number gutter. It numbers actual text
lines, including headings and blank lines, rather than wrapped screen rows.
Resizing or scrolling does not renumber unchanged text. Appending messages keeps
existing numbers; replacing the chat with a successful Distill starts numbering
its new text from 1. Reopening the same unchanged revision gives the same lines.
Numbers are visual only: they are not inserted into copied text, model requests,
or exported files, and are not permanent references across future distills.

**Save revision…**, beside the revision title, exports the selected revision's
current active conversation as **PDF**, **Text (.txt)**, or **Markdown (.md)**.
Choose the type in the save dialog. “Markup” is implemented as Markdown.
Exports include project/revision titles, the current distilled context, and later
messages in order. Unsent drafts and older archived history are excluded.
Saving an export does not create, replace, or change a revision.

PDF keeps ordinary text searchable and renders emoji as small embedded glyph images,
so emoji are visible but are not returned by PDF text extraction. Text and Markdown
keep the original Unicode characters.

The selected type determines the final file extension. Replacing an existing
file requires explicit confirmation of that final path. Export files are staged
before publication, so a rendering/write failure leaves the previous file intact.
The app prevents exporting over its own chat-storage files. Save to a normal
folder such as Documents instead. Export is unavailable during a model request.

## Model settings

Start the LM Studio OpenAI-compatible local server and load a model. Under
**Model settings**, select a model from the automatically discovered list or
enter an identifier manually. Use **Refresh models** after changing models on
the server. Distil does not choose a model for you. The default URL is
`http://127.0.0.1:1234`. The URL accepts the server root, `/v1`, or
`/v1/chat/completions`.

In **Model settings**, leave **Maximum output tokens** blank to use the server
default, or enter a limit for generated tokens. This does not limit or trim the
input conversation. Input and output must fit the loaded model's context; if the
server reports that generation reached its limit, Distil keeps the original
chat and does not save the incomplete result. **Timeout (seconds)** controls
how long the request can run. Both fields apply for the current app session.
Settings can also be supplied when launching:

- `LMSTUDIO_HOST`
- `LMSTUDIO_MODEL`
- `LMSTUDIO_API_KEY` (optional; never written to Distil storage or logs)
- `LMSTUDIO_REQUEST_TIMEOUT` (seconds, default 600)

Server/model edits in the window apply to that run. On startup and after the
server URL changes, Distil requests the server's model list in the background;
it does not send chat content or start inference. Pressing Send or Distill
transmits the current active chat to the
configured server. This version requires OpenAI-compatible streaming responses
with a successful finish reason and terminal `[DONE]`; incomplete, empty,
filtered, or length-limited output is not saved.

## Storage and safety

Data defaults to `~/hornet_distil`, isolated from all original Hornet data.
Override with `HORNET_DISTIL_ROOT`. Back up this directory as a unit while the
app is closed.

- `distil.sqlite3`: projects, revisions, active chats, drafts, immutable message
  and distillation events, and soft-deletion flags.
- `revisions/<revision-id>/distilled.md`: exactly one current human-readable
  distilled artifact per revision, created by import or successful Distill.

SQLite is the recovery authority. Do not edit `distilled.md` in place expecting
the chat to change: use Import. On startup, an interrupted artifact write is
reconciled to the last committed database state. Writes are staged atomically;
normal save failures roll back both state and file. Concurrent edits detect a
revision version conflict instead of silently overwriting it.

During model requests, editing and navigation are disabled. **Stop** immediately
restores the UI and rejects any late result. Streaming reads are interrupted;
before HTTP headers arrive, stopping the network itself is best-effort. No
partial model text is saved. On a failed/stopped Distill, the active chat and
saved distilled file remain unchanged. A failed/stopped Send keeps the user's
message. Closing during generation cancels it. If saving a draft fails,
navigation and closing are blocked rather than discarding the text.

This is retained local history, not encrypted storage. Soft deletion is not
permanent erasure. Keep backups of important conversations and verify distilled
content before relying on it.

## Checks

Focused backend, streaming mock-server, and offscreen UI tests:

```sh
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests/distil -v
```

Run the Coder/core aggregate separately:

```sh
python3 -m unittest discover -s tests -v
```

The optional Distil tests run separately from core tests. Mock tests do not
establish live LM Studio/model quality or reliable oversized-context behavior.
