"""Explicit folder selection and confirmation; native drag detection is deferred."""
from tkinter import filedialog, messagebox
from .revision_export import DestinationChanged, plan_export


def confirm_export(app, project, revision, parent=None):
    if app.busy or not project or not revision or getattr(app, '_export_active', False):
        return
    app._export_active = True
    try:
        if parent is None:
            parent = filedialog.askdirectory(title='Choose where to save the complete revision', parent=app.root)
        if not parent:
            return
        while True:
            destination = plan_export(app.ws, project, revision, parent)
            if not messagebox.askyesno('Export revision?',
                    f'Copy the complete revision {project} / {revision} to:\n\n{destination}\n\n'
                    'The revision and original source stay unchanged. Existing folders will not be overwritten.\n\n'
                    'Are you sure?', parent=app.root, default='no'):
                return
            if app.busy:
                return
            try:
                saved = app.ws.export_revision(project, revision, parent, destination=destination)
                break
            except DestinationChanged:
                # Never silently change a destination after the user approves it.
                continue
        app.status.set('Revision saved: ' + str(saved))
        messagebox.showinfo('Revision saved', 'Saved the complete revision to:\n' + str(saved), parent=app.root)
    except (OSError, ValueError) as exc:
        messagebox.showerror('Revision export', str(exc), parent=app.root)
    finally:
        app._export_active = False
