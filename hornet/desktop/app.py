"""Native desktop interface. Run with python -m hornet.desktop."""
import argparse
import difflib
from datetime import datetime
import json
from pathlib import Path
import queue
import re
import shlex
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog, font as tkfont

from .workspace import Workspace, source_files, read_source, request_proposal, discover_models, write_json
from .layouts import LAYOUTS
from .file_selection import default_excluded
from .revision_display import ensure_display, rename_revision, revision_parent
from .vm_runner import run_python, diagnostic_for_mission, run_hint
from .automation import (read_state, require_ready, make_initialization_draft, accept_initialization,
                         model_describer, prepare_mission, accept_mission, diagnose_mission)


class App:
    def __init__(self, root, workspace):
        self.root, self.ws = root, workspace
        self.project = self.revision = None
        self.pending = None
        self.pending_initialization = None
        self.pending_mission = None
        self.busy = False
        self.events = queue.Queue()
        self.revision_icons = {}
        from hornet import __version__
        root.title(f'HORNET · Mission Control · {__version__}')
        root.geometry('1280x850')
        root.minsize(1000, 650)
        style = ttk.Style()
        style.theme_use('clam')
        tree_font = tkfont.Font(root=root, font=style.lookup('Treeview', 'font') or 'TkDefaultFont')
        # The theme's fixed-height rows can be shorter than an accessibility-scaled font.
        style.configure('Treeview', rowheight=tree_font.metrics('linespace') + int(root.winfo_fpixels('4p')))
        style.configure('TFrame', background='#17212b')
        style.configure('TLabel', background='#17212b', foreground='#eef2f5')
        style.configure('TButton', padding=(6, 3))
        bar = ttk.Frame(root, padding=10)
        bar.pack(fill='x')
        ttk.Label(bar, text='HORNET  /  MISSION CONTROL', font=('', 16, 'bold')).pack(side='left')
        self.new_project_button = ttk.Button(bar, text='New project', command=self.new_project)
        self.new_project_button.pack(side='right', padx=4)
        self.import_button = ttk.Button(bar, text='Import folder', command=lambda: self.new_project(True))
        self.import_button.pack(side='right')
        panes = ttk.Panedwindow(root, orient='horizontal')
        left = ttk.Frame(panes, padding=8)
        panes.add(left, weight=1)
        ttk.Label(left, text='PROJECTS & REVISIONS').pack(anchor='w', pady=5)
        ttk.Label(left, text='Choose a project or revision').pack(anchor='w')
        project_tree = ttk.Frame(left)
        project_tree.pack(fill='both', expand=True)
        self.projects = ttk.Treeview(project_tree, show='tree', selectmode='browse', height=6)
        project_vertical = ttk.Scrollbar(project_tree, orient='vertical', command=self.projects.yview)
        project_vertical.pack(side='right', fill='y')
        self.projects.configure(yscrollcommand=project_vertical.set)
        project_scroll = ttk.Scrollbar(project_tree, orient='horizontal', command=self.projects.xview)
        self.projects.configure(xscrollcommand=project_scroll.set)
        self.projects.column('#0', width=280, minwidth=160, stretch=True)
        project_scroll.pack(side='bottom', fill='x')
        self.projects.pack(fill='both', expand=True)
        self.projects.bind('<<TreeviewSelect>>', self.choose_revision)
        self.projects.bind('<Button-3>', self.revision_context_menu)
        project_actions = ttk.LabelFrame(left, text='Project setup', padding=4)
        project_actions.pack(fill='x', pady=(6, 0))
        project_actions.columnconfigure((0, 1), weight=1, uniform='project-actions')
        self.init_button = ttk.Button(project_actions, text='Initialize', command=self.initialize_project)
        self.init_button.grid(row=0, column=0, sticky='ew', padx=2, pady=2)
        self.accept_map = ttk.Button(project_actions, text='Accept map', command=self.accept_project_map, state='disabled')
        self.accept_map.grid(row=0, column=1, sticky='ew', padx=2, pady=2)
        self.edit_map = ttk.Button(project_actions, text='Edit map', command=self.edit_project_map, state='disabled')
        self.edit_map.grid(row=1, column=0, sticky='ew', padx=2, pady=2)
        self.review_button = ttk.Button(project_actions, text='Review pending', command=self.review_pending)
        self.review_button.grid(row=1, column=1, sticky='ew', padx=2, pady=2)
        revision_actions = ttk.LabelFrame(left, text='Revision and project', padding=4)
        revision_actions.pack(fill='x', pady=(6, 0))
        revision_actions.columnconfigure((0, 1), weight=1, uniform='revision-actions')
        self.copy_button = ttk.Button(revision_actions, text='Copy revision', command=self.copy_revision)
        self.copy_button.grid(row=0, column=0, sticky='ew', padx=2, pady=2)
        self.revision_menu_button = ttk.Menubutton(revision_actions, text='Revision…')
        self.revision_menu_button.grid(row=0, column=1, sticky='ew', padx=2, pady=2)
        self.revision_menu = tk.Menu(self.revision_menu_button, tearoff=False)
        self.revision_menu.add_command(label='Rename revision…', command=lambda: self.rename_revision(self.project, self.revision))
        self.revision_menu.add_command(label='Save revision to disk…', command=lambda: self.export_revision(self.project, self.revision))
        self.revision_menu.add_command(label='Move revision to trash…', command=self.trash_revision)
        self.revision_menu_button.configure(menu=self.revision_menu)
        self.delete_project_button = ttk.Button(revision_actions, text='Delete project…', command=self.delete_project)
        self.delete_project_button.grid(row=1, column=0, columnspan=2, sticky='ew', padx=2, pady=2)
        # Reserve space for grouped controls before the tree takes remaining room.
        project_tree.pack_forget()
        project_actions.pack_forget()
        revision_actions.pack_forget()
        revision_actions.pack(side='bottom', fill='x', pady=(6, 0))
        project_actions.pack(side='bottom', fill='x', pady=(6, 0))
        project_tree.pack(fill='both', expand=True)
        middle = ttk.Frame(panes, padding=8)
        panes.add(middle, weight=1)
        ttk.Label(middle, text='FILES  ·  Ctrl-click for diagnosis').pack(anchor='w', pady=5)
        files_frame = ttk.Frame(middle)
        files_frame.pack(fill='both', expand=True)
        self.files = ttk.Treeview(files_frame, show='tree', selectmode='extended')
        files_vertical = ttk.Scrollbar(files_frame, orient='vertical', command=self.files.yview)
        files_vertical.pack(side='right', fill='y')
        self.files.configure(yscrollcommand=files_vertical.set)
        self.files.pack(fill='both', expand=True)
        self.files.bind('<<TreeviewSelect>>', self.preview)
        right = ttk.Frame(panes, padding=8)
        panes.add(right, weight=4)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill='both', expand=True)
        self.views = {}
        for label in ('Source', 'Shadow', 'Blueprint', 'Proposal', 'Run output', 'Activity'):
            frame = ttk.Frame(self.tabs)
            self.tabs.add(frame, text=label)
            text = tk.Text(frame, wrap='word' if label == 'Proposal' else 'none',
                           background='#101820', foreground='#e9eef2',
                           insertbackground='white', font=('monospace', 11), padx=12, pady=10)
            scroll = ttk.Scrollbar(frame, orient='vertical', command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            scroll.pack(side='right', fill='y')
            if label != 'Proposal':
                horizontal = ttk.Scrollbar(frame, orient='horizontal', command=text.xview)
                horizontal.pack(side='bottom', fill='x')
                text.configure(xscrollcommand=horizontal.set)
            text.pack(fill='both', expand=True)
            self.views[label] = text
            text.configure(state='disabled')
        self.blueprint_button = ttk.Button(right, text='Edit blueprint into a new revision', command=self.edit_blueprint)
        self.tabs.pack_forget()
        self.blueprint_button.pack(side='bottom', anchor='e', pady=4)
        self.tabs.pack(fill='both', expand=True)
        mission = ttk.LabelFrame(root, text='Mission and model', padding=(10, 4))
        ttk.Label(mission, text='Missions send project metadata and model-selected files to the configured server.').pack(anchor='w')
        self.goal = tk.Text(mission, height=2, font=('', 11))
        self.goal.pack(fill='x', pady=5)
        # File scope for automated missions is planned from the blueprint.
        self.new_files = tk.StringVar()
        self.include_run = tk.BooleanVar(value=False)
        checks_row = ttk.Frame(mission)
        checks_row.pack(fill='x', pady=(0, 5))
        ttk.Checkbutton(checks_row, text='Include latest VM output', variable=self.include_run).pack(side='left')
        ttk.Label(checks_row, text='VM check (blank skips)').pack(side='left', padx=(12, 0))
        self.check_command = tk.StringVar(value='')
        ttk.Entry(checks_row, textvariable=self.check_command).pack(side='left', fill='x', expand=True, padx=6)
        mission_actions = ttk.Frame(mission)
        mission_actions.pack(fill='x', pady=(0, 5))
        self.auto_button = ttk.Button(mission_actions, text='Run mission', command=self.run_mission)
        self.auto_button.pack(side='left')
        self.diagnose_button = ttk.Button(mission_actions, text='Diagnose only', command=self.run_diagnosis)
        self.diagnose_button.pack(side='left', padx=6)
        self.accept_mission_button = ttk.Button(mission_actions, text='Accept mission', command=self.accept_automated_mission, state='disabled')
        self.accept_mission_button.pack(side='left', padx=6)
        self.run_button = ttk.Button(mission_actions, text='Run in Test VM', command=self.run_project)
        self.run_button.pack(side='left', padx=6)
        goal_actions = ttk.Frame(mission_actions)
        goal_actions.pack(side='right')
        ttk.Button(goal_actions, text='Copy', command=self.copy_mission).pack(side='left')
        ttk.Button(goal_actions, text='Paste', command=self.paste_mission).pack(side='left', padx=4)
        ttk.Button(goal_actions, text='Clear', command=self.clear_mission).pack(side='left')
        connection = ttk.Frame(mission)
        connection.pack(fill='x')
        self.endpoint = tk.StringVar(value='http://localhost:1234/v1')
        self.model = tk.StringVar(value=self.load_saved_model())
        ttk.Label(connection, text='LM Studio URL').pack(side='left')
        ttk.Entry(connection, textvariable=self.endpoint, width=35).pack(side='left', padx=6)
        ttk.Label(connection, text='Model ID').pack(side='left')
        self.models = ttk.Combobox(connection, textvariable=self.model, width=25)
        self.models.pack(side='left', padx=6)
        self.models.bind('<<ComboboxSelected>>', self.save_model)
        self.models.bind('<FocusOut>', self.save_model)
        self.find_models = ttk.Button(connection, text='Find models', command=self.load_models)
        self.find_models.pack(side='left', padx=4)
        # Retain the legacy widgets for compatibility with stored UI state, but
        # do not expose the full-file proposal path as an agent editing route.
        self.send = ttk.Button(connection, text='Prepare proposal', command=self.prepare, state='disabled')
        self.apply_button = ttk.Button(connection, text='Apply to new revision', command=self.apply, state='disabled')
        self.activity_indicator = ttk.Progressbar(root, mode='indeterminate', length=150)
        self.activity_indicator.pack(fill='x', padx=8)
        self.status = tk.StringVar(value='Start by creating a project or importing a folder.')
        self.status_label = ttk.Label(root, textvariable=self.status, padding=6, wraplength=900)
        self.status_label.pack(side='bottom', fill='x')
        self.activity_indicator.pack_forget()
        self.activity_indicator.pack(side='bottom', fill='x', padx=8)
        mission.pack(side='bottom', fill='x', padx=8, pady=(0, 4))
        panes.pack(fill='both', expand=True)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()
        self.update_gate()
        root.after(100, self.poll)

    def load_saved_model(self):
        path = self.ws.root / 'desktop-settings.json'
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            return value.get('model', '') if isinstance(value, dict) and isinstance(value.get('model', ''), str) else ''
        except (OSError, ValueError):
            return ''

    def save_model(self, event=None):
        path = self.ws.root / 'desktop-settings.json'
        try:
            try:
                settings = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(settings, dict):
                    return
            except FileNotFoundError:
                settings = {}
            settings['model'] = self.model.get().strip()
            write_json(path, settings)
        except (OSError, ValueError):
            self.status.set('Could not save model selection; check workspace settings.')

    def show(self, label, content):
        view = self.views[label]
        view.configure(state='normal')
        view.delete('1.0', 'end')
        view.insert('1.0', content)
        view.configure(state='disabled')

    def activity(self, message, clear=False):
        view = self.views['Activity']
        view.configure(state='normal')
        if clear:
            view.delete('1.0', 'end')
        view.insert('end', datetime.now().strftime('%H:%M:%S') + '  ' + message + '\n')
        view.see('end')
        view.configure(state='disabled')

    def copy_mission(self):
        try:
            content = self.goal.get('sel.first', 'sel.last')
        except tk.TclError:
            content = self.goal.get('1.0', 'end-1c')
        if content:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.status.set('Mission text copied.')

    def paste_mission(self):
        try:
            content = self.root.clipboard_get()
        except tk.TclError:
            self.status.set('Clipboard has no text to paste.')
            return
        try:
            start = self.goal.index('sel.first')
            self.goal.delete('sel.first', 'sel.last')
            self.goal.mark_set('insert', start)
        except tk.TclError:
            pass
        self.goal.insert('insert', content)
        self.goal.focus_set()
        self.status.set('Mission text pasted at the cursor.')

    def clear_mission(self):
        self.goal.delete('1.0', 'end')
        self.goal.focus_set()
        self.status.set('Mission text cleared.')

    def guard(self, action):
        try:
            return action()
        except Exception as exc:
            messagebox.showerror('Hornet', str(exc))

    def revision_icon(self, color, parent_color):
        key = (color, parent_color)
        if key not in self.revision_icons:
            icon = tk.PhotoImage(master=self.root, width=32, height=14)
            for center, fill in ((7, color), (24, parent_color)):
                if not fill:
                    continue
                for y in range(1, 14):
                    for x in range(center-6, center+7):
                        distance = (x-center)**2 + (y-7)**2
                        if distance <= 36:
                            icon.put('#202020' if distance >= 25 else fill, (x, y))
            self.revision_icons[key] = icon
        return self.revision_icons[key]

    def refresh(self, project=None, revision=None):
        self.projects.delete(*self.projects.get_children())
        self.revision_icons.clear()
        for name in self.ws.list_projects():
            item = self.projects.insert('', 'end', text=name, values=(name,), open=True)
            accepted = read_state(self.ws, name).get('accepted_revision')
            display = ensure_display(self.ws, name)
            for rev in self.ws.revisions(name):
                parent = revision_parent(self.ws, name, rev)
                own_id = rev.rsplit('-', 1)[-1]
                parent_id = parent.rsplit('-', 1)[-1] if parent else 'root'
                custom_name = display[rev].get('name', '')
                label = (('[ACCEPTED]  ' if rev == accepted else '') + own_id + ' ← ' + parent_id
                         + '  ' + (custom_name or rev))
                if custom_name:
                    label += '  (' + rev + ')'
                icon = self.revision_icon(display[rev]['color'], display[parent]['color'] if parent else None)
                row = self.projects.insert(item, 'end', text=label, image=icon, values=(name, rev))
                if (name, rev) == (project, revision):
                    self.projects.selection_set(row)
                    self.projects.see(row)

    def new_project(self, importing=False):
        if self.busy:
            return
        source = filedialog.askdirectory(title='Import a source folder (hidden files, environments and Git metadata excluded)') if importing else None
        if importing and not source:
            return
        name = simpledialog.askstring('New project', 'Project name (letters, numbers, hyphens, underscores):')
        if not name:
            return
        if importing:
            def create_import(selected, _resume):
                def perform():
                    self.refresh(name, self.ws.create(name, source=source, include_files=selected))
                    self.status.set('Project imported. Select its revision to initialize it.')
                self.guard(perform)
            self.choose_project_files(Path(source), 'Choose files to import',
                                      'Only selected files will be copied into Hornet. '
                                      'Chat data, memories, backups and common credential files start unselected. '
                                      'Review every selection before importing.', create_import)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('Choose project layout')
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Starter recipe for ' + name).pack(anchor='w', pady=6)
        keys = list(LAYOUTS)
        selection = ttk.Combobox(frame, state='readonly', width=45,
                                 values=[LAYOUTS[key][0] for key in keys])
        selection.pack(fill='x')
        selection.current(1)
        description = tk.StringVar()
        def explain(_=None):
            description.set(LAYOUTS[keys[selection.current()]][1])
        selection.bind('<<ComboboxSelected>>', explain)
        explain()
        ttk.Label(frame, textvariable=description, wraplength=460).pack(fill='x', pady=14)
        ttk.Label(frame, text='The recipe and directory rules will be saved in the blueprint.',
                  wraplength=460).pack(anchor='w')
        def create():
            def perform():
                rev = self.ws.create(name, keys[selection.current()])
                self.refresh(name, rev)
                dialog.destroy()
            self.guard(perform)
        ttk.Button(frame, text='Create project', command=create).pack(anchor='e', pady=12)

    def choose_project_files(self, root, title, explanation, on_continue, selected=None, allow_restart=False):
        names = list(source_files(root))
        if not names:
            messagebox.showerror(title, 'No importable files were found.', parent=self.root)
            return
        chosen = set(selected) if selected is not None else {n for n in names if not default_excluded(n)}
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.geometry('850x650')
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=explanation, wraplength=780, justify='left').pack(anchor='w', pady=(0, 8))
        ttk.Label(frame, text='Selected rows are included. Click a row to include or exclude it.').pack(anchor='w')
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill='both', expand=True, pady=8)
        listing = tk.Listbox(list_frame, selectmode='multiple', exportselection=False, font=('TkDefaultFont', 12))
        scroll = ttk.Scrollbar(list_frame, orient='vertical', command=listing.yview)
        listing.configure(yscrollcommand=scroll.set)
        listing.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        for index, name in enumerate(names):
            listing.insert('end', name)
            if name in chosen:
                listing.selection_set(index)
        controls = ttk.Frame(frame)
        controls.pack(fill='x')
        def proceed(resume):
            picked = [names[i] for i in listing.curselection()]
            if not picked:
                messagebox.showerror(title, 'Select at least one file.', parent=dialog)
                return
            dialog.destroy()
            on_continue(picked, resume)
        ttk.Button(controls, text='Cancel', command=dialog.destroy).pack(side='right', padx=5)
        ttk.Button(controls, text='Continue', command=lambda: proceed(True)).pack(side='right', padx=5)
        if allow_restart:
            ttk.Button(controls, text='Start over', command=lambda: proceed(False)).pack(side='right', padx=5)
        dialog.grab_set()

    def choose_revision(self, _=None):
        selected = self.projects.selection()
        if not selected:
            return
        values = self.projects.item(selected[0], 'values')
        if self.busy:
            # Background work is bound to the selection that started it. Keep
            # the tree and displayed project in sync until that work completes.
            if tuple(values) != (self.project, self.revision):
                for project_row in self.projects.get_children(''):
                    for row in self.projects.get_children(project_row):
                        if tuple(self.projects.item(row, 'values')) == (self.project, self.revision):
                            self.projects.selection_set(row)
                            self.projects.see(row)
                            self.status.set('Wait for the current task to finish before changing revisions.')
                            return
            return
        previous = (self.project, self.revision)
        self.project = values[0]
        self.revision = values[1] if len(values) == 2 else None
        if self.revision is None:
            state = read_state(self.ws, self.project)
            default = state.get('accepted_revision') or next(iter(self.ws.revisions(self.project)), None)
            if default:
                for child in self.projects.get_children(selected[0]):
                    if tuple(self.projects.item(child, 'values')) == (self.project, default):
                        self.projects.selection_set(child)
                        return
        draft_path = self.ws.project(self.project) / 'initialization-draft.json'
        if draft_path.is_file():
            self.pending_initialization = json.loads(draft_path.read_text(encoding='utf-8'))
        elif self.pending_initialization and self.pending_initialization.get('project') != self.project:
            self.pending_initialization = None
        if previous != (self.project, self.revision):
            draft_matches = (self.pending_initialization and
                             (self.pending_initialization.get('project'), self.pending_initialization.get('revision')) == (self.project, self.revision))
            mission_matches = (self.pending_mission and
                               (self.pending_mission.get('project'), self.pending_mission.get('final_revision')) == (self.project, self.revision))
            if not (draft_matches or mission_matches):
                self.show('Proposal', '')
        self.update_gate()
        self.files.delete(*self.files.get_children())
        for key in ('Source', 'Shadow', 'Blueprint'):
            self.show(key, '')
        if not self.revision:
            return
        root = self.ws.revision(self.project, self.revision)
        folders = {'': ''}
        for name in source_files(root):
            path = Path(name)
            for parent in reversed(path.parents):
                key = parent.as_posix()
                if key == '.':
                    continue
                if key not in folders:
                    par = parent.parent.as_posix()
                    folders[key] = self.files.insert(folders.get(par, ''), 'end', text=parent.name, open=True)
            self.files.insert(folders.get(path.parent.as_posix(), ''), 'end', text=path.name, values=(name,))
        self.show('Blueprint', (root / '.blueprint').read_text())
        try:
            require_ready(self.ws, self.project, self.revision)
        except ValueError as exc:
            self.status.set(str(exc))
        else:
            self.status.set(f'{self.project} / {self.revision} · Select files for diagnosis; missions use model-planned scope.')

    def update_gate(self):
        selected = bool(self.project and self.revision)
        project_ready = False
        revision_ready = False
        if selected:
            try:
                require_ready(self.ws, self.project)
                project_ready = True
                require_ready(self.ws, self.project, self.revision)
                revision_ready = True
            except ValueError:
                pass
        for button in (self.diagnose_button, self.auto_button, self.run_button, self.copy_button, self.blueprint_button):
            button.configure(state='normal' if revision_ready and not self.busy else 'disabled')
        self.delete_project_button.configure(state='normal' if self.project and not self.busy else 'disabled')
        if hasattr(self, 'new_project_button'):
            self.new_project_button.configure(state='disabled' if self.busy else 'normal')
            self.import_button.configure(state='disabled' if self.busy else 'normal')
            self.revision_menu_button.configure(state='normal' if selected and not self.busy else 'disabled')
            self.review_button.configure(state='normal' if self.project and not self.busy else 'disabled')
        if hasattr(self, 'init_button'):
            self.init_button.configure(state='normal' if selected and (not project_ready or revision_ready) and not self.busy else 'disabled')
        draft_ready = (self.pending_initialization and self.pending_initialization.get('version') == 2
                       and self.pending_initialization.get('project') == self.project
                       and self.pending_initialization.get('revision') == self.revision
                       and not self.pending_initialization.get('unreviewed')
                       and not self.pending_initialization.get('issues'))
        self.accept_map.configure(state='normal' if draft_ready and not self.busy else 'disabled')
        self.edit_map.configure(state='normal' if self.pending_initialization and self.pending_initialization.get('project') == self.project and self.pending_initialization.get('revision') == self.revision and not self.busy else 'disabled')
        can_accept = (self.pending_mission and self.pending_mission.get('project') == self.project
                      and self.pending_mission.get('final_revision') == self.revision
                      and self.pending_mission.get('state') == 'review'
                      and (not self.pending_mission.get('check_command') or self.pending_mission.get('checks_passed')))
        self.accept_mission_button.configure(state='normal' if can_accept and not self.busy else 'disabled')

    def show_initialization(self, value):
        self.show('Proposal', json.dumps({'project': value.get('project'), 'base_revision': value.get('revision'),
                                          'included_files': value.get('included', []),
                                          'excluded_files': value.get('excluded', []),
                                          'blueprint': value['blueprint'], 'summaries': value['summaries'],
                                          'unreviewed_files': value['unreviewed'],
                                          'issues': value.get('issues', {}),
                                          'review_notes': value.get('review_notes', [])}, indent=2))
        self.tabs.select(3)
        if (value.get('project'), value.get('revision')) != (self.project, self.revision):
            self.status.set('Select the project map’s base revision before editing or accepting it.')
        elif value.get('version') != 2:
            self.status.set('This older draft needs to be initialized again before acceptance.')
        elif value['unreviewed'] or value.get('issues'):
            self.status.set('Initialization incomplete. Select Initialize to retry the missing parts.')
        else:
            self.status.set('Initialization complete. Review the project map, then accept it.')

    def show_diagnosis(self, value):
        report = value['report']
        content = (f"Diagnosis for {value['project']} / {value['base_revision']}\n\n"
                   f"--- Diagnosis ---\n{report['diagnosis']}\n\n"
                   f"--- Evidence ---\n{report['evidence']}\n\n"
                   f"--- Recommended next steps ---\n{report['recommended_next_steps']}\n\n"
                   f"Selected source files: {', '.join(value.get('selected_files', [])) or 'None'}\n"
                   "No source changes or revision were created.\n")
        self.show('Proposal', content)
        self.tabs.select(3)
        self.status.set('Diagnosis complete. Review the report; the revision is unchanged.')

    def show_mission(self, value):
        report = {'mission': value['mission'], 'base_revision': value['base_revision'],
                  'candidate_revision': value['final_revision'], 'changed_files': value.get('changed_files', []),
                  'agent_summary': value.get('proposal', {}).get('summary'), 'repairs': value.get('repairs', 0),
                  'checks_passed': value['checks_passed'], 'checks': value.get('checks', []),
                  'tool_receipts': value.get('tool_receipts', [])}
        if value.get('recovery_note'):
            report['recovery_note'] = value['recovery_note']
        base = self.ws.revision(value['project'], value['base_revision'])
        candidate = self.ws.revision(value['project'], value['final_revision'])
        changes = []
        for name in value.get('changed_files', []):
            try:
                old = read_source(base, name) if (base / name).is_file() else ''
                new = read_source(candidate, name)
                changes.extend(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                   fromfile=name + ' (before)', tofile=name + ' (candidate)'))
            except (ValueError, OSError, UnicodeError) as exc:
                changes.append(f'\nCannot show diff for {name}: {exc}\n')
        old_blueprint = (base / '.blueprint').read_text(encoding='utf-8')
        new_blueprint = (candidate / '.blueprint').read_text(encoding='utf-8')
        blueprint_diff = ''.join(difflib.unified_diff(old_blueprint.splitlines(True), new_blueprint.splitlines(True),
                                                       fromfile='blueprint (before)', tofile='blueprint (candidate)'))
        shadows = json.loads((candidate / '.hornet-meta' / 'shadows.json').read_text(encoding='utf-8'))
        updated_shadows = {name: shadows.get(name, {}) for name in value.get('changed_files', [])}
        checks = value.get('checks', [])
        if not value.get('check_command'):
            check_summary = 'SKIPPED'
        elif value.get('checks_passed'):
            check_summary = 'PASSED'
        else:
            check_summary = 'FAILED'
        check_text = '\n'.join(str(c.get('stdout', '')) + '\n' + str(c.get('stderr', '')) for c in checks)
        test_counts = re.findall(r'Ran\s+\d+\s+tests?', check_text)
        retries = sum(r.get('uri') == 'hornet.plan_preflight' and r.get('status') == 'RETRY' for r in value.get('tool_receipts', []))
        summary = ('MISSION REVIEW\n' + 'VM check: ' + check_summary +
                   ((' · ' + test_counts[-1]) if test_counts else '') +
                   '\nChanged files: ' + (', '.join(value.get('changed_files', [])) or 'None') +
                   '\nPlan corrections: ' + str(retries) + ' · Check repairs: ' + str(value.get('repairs', 0)) +
                   '\nCandidate: ' + value['final_revision'] +
                   '\nReview the changes below before accepting.\n\n--- Details ---\n')
        self.show('Proposal', summary + json.dumps(report, indent=2) + '\n\n--- Source changes ---\n' + ''.join(changes)
                  + '\n\n--- Blueprint changes ---\n' + (blueprint_diff or 'No blueprint changes.\n')
                  + '\n\n--- Updated shadows ---\n' + json.dumps(updated_shadows, indent=2))
        view = self.views['Proposal']
        summary_font = tkfont.nametofont('TkDefaultFont').copy()
        summary_font.configure(size=max(13, abs(summary_font.cget('size'))), weight='bold')
        self.summary_font = summary_font
        view.tag_configure('mission_summary', font=summary_font, spacing1=3, spacing3=3)
        view.tag_add('mission_summary', '1.0', '7.0')
        self.tabs.select(3)
        if (value.get('project'), value.get('final_revision')) != (self.project, self.revision):
            self.status.set('Select the mission candidate revision before accepting it.')
        elif value.get('check_command') and not value.get('checks_passed'):
            self.status.set('The check failed. Inspect the report; the accepted revision is unchanged.')
        elif not value.get('check_command'):
            self.status.set('Review mission results. Checks were skipped for this mission.')
        else:
            self.status.set('Review mission results; accept only after inspection.')

    def review_pending(self):
        if not self.project or self.busy:
            return
        def perform():
            project = self.ws.project(self.project)
            draft = project / 'initialization-draft.json'
            if draft.is_file():
                self.pending_initialization = json.loads(draft.read_text(encoding='utf-8'))
                self.show_initialization(self.pending_initialization)
                self.update_gate()
                return
            directory = project / 'missions'
            if directory.is_dir():
                records = [json.loads(path.read_text(encoding='utf-8')) for path in
                           sorted(directory.glob('*.json'), key=lambda path: path.stat().st_mtime, reverse=True)]
                revisions = set(self.ws.revisions(self.project))
                def available_review(value):
                    return (value.get('state') == 'review' and value.get('project') == self.project
                            and value.get('base_revision') in revisions and value.get('final_revision') in revisions)
                # Prefer the proposal belonging to the selected candidate. This
                # allows parallel branches to be reviewed independently.
                selected = next((value for value in records if available_review(value)
                                 and value.get('final_revision') == self.revision), None)
                if selected:
                    self.pending_mission = selected
                    self.show_mission(selected)
                    self.update_gate()
                    return
                interrupted = None
                for value in records:
                    if value.get('state') == 'diagnosis_complete' and value.get('project') == self.project:
                        self.show_diagnosis(value)
                        return
                    if available_review(value):
                        self.pending_mission = value
                        self.show_mission(value)
                        self.update_gate()
                        return
                    if interrupted is None and value.get('project') == self.project and value.get('state') not in ('accepted', 'review'):
                        interrupted = value
                if interrupted:
                    self.show('Proposal', json.dumps(interrupted, indent=2))
                    self.tabs.select(3)
                    self.status.set('This mission stopped or was interrupted. Inspect the record; the accepted revision is unchanged.')
                    return
            raise ValueError('There is no pending project map or mission to review.')
        self.guard(perform)

    def initialize_project(self):
        if not self.project or not self.revision or self.busy:
            return
        project, revision = self.project, self.revision
        endpoint, model = self.endpoint.get().strip(), self.model.get().strip()
        if not model:
            messagebox.showerror('Initialize project', 'Choose a model first.')
            return
        previous = self.pending_initialization
        if not previous:
            draft_path = self.ws.project(project) / 'initialization-draft.json'
            if draft_path.is_file():
                previous = json.loads(draft_path.read_text(encoding='utf-8'))
        previous_selection = (previous.get('included') if previous and previous.get('version') == 2
                              and previous.get('project') == project and previous.get('revision') == revision else None)
        def begin(selected, resume):
            self.busy = True
            self.pending_initialization = None
            self.update_gate()
            self.activity('Initializing project map.', clear=True)
            self.activity_indicator.start(12)
            self.status.set('Initializing project map; progress is saved after each part.')
            def worker():
                try:
                    draft = make_initialization_draft(self.ws, project, revision, model_describer(endpoint, model),
                                                      lambda phase: self.events.put(('automation_progress', phase)),
                                                      included=selected, resume=resume)
                    self.events.put(('initialized_draft', draft))
                except Exception as exc:
                    self.events.put(('automation_error', str(exc)))
            threading.Thread(target=worker, daemon=True).start()
        self.choose_project_files(self.ws.revision(project, revision), 'Initialize project · choose files',
                                  'The selected files and blueprint will be sent to the model server at '
                                  + endpoint + '. Review private files before continuing. '
                                  'Previously completed parts are reused unless you choose Start over.',
                                  begin, selected=previous_selection, allow_restart=True)

    def accept_project_map(self):
        if not self.pending_initialization or self.busy:
            return
        def perform():
            project = self.pending_initialization['project']
            if (project, self.pending_initialization.get('revision')) != (self.project, self.revision):
                raise ValueError('Select the revision used for this project map before accepting it.')
            path = self.ws.project(project) / 'initialization-draft.json'
            if json.loads(path.read_text(encoding='utf-8')) != self.pending_initialization:
                raise ValueError('The saved project map changed after review. Review it again before accepting.')
            revision = accept_initialization(self.ws, project)
            self.pending_initialization = None
            self.refresh(project, revision)
            self.status.set('Project initialized. Enter a mission or run the project.')
            self.update_gate()
        self.guard(perform)

    def edit_project_map(self):
        if (not self.pending_initialization or self.busy or
                (self.pending_initialization.get('project'), self.pending_initialization.get('revision')) != (self.project, self.revision)):
            return
        draft = self.pending_initialization
        dialog = tk.Toplevel(self.root)
        dialog.title('Edit project map · JSON')
        editor = tk.Text(dialog, width=100, height=30)
        editor.pack(fill='both', expand=True)
        editor.insert('1.0', json.dumps({'blueprint': draft['blueprint'], 'summaries': draft['summaries']}, indent=2))
        def save():
            def perform():
                if self.busy or (self.project, self.revision) != (draft['project'], draft['revision']) or self.pending_initialization is not draft:
                    raise ValueError('The selected project or pending map changed. Review it again before saving.')
                value = json.loads(editor.get('1.0', 'end'))
                if not isinstance(value, dict) or not isinstance(value.get('blueprint'), dict) or not value['blueprint'] or len(json.dumps(value['blueprint']).encode('utf-8')) > 128_000:
                    raise ValueError('Blueprint must be a nonempty JSON object under 128 KB.')
                summaries = value.get('summaries')
                if not isinstance(summaries, dict) or any(name not in draft['source_sha256'] or not isinstance(text, str) or not text.strip() or len(text) > 4000 for name, text in summaries.items()):
                    raise ValueError('Summaries must map project paths to nonempty descriptions under 4,000 characters.')
                path = self.ws.project(draft['project']) / 'initialization-draft.json'
                if json.loads(path.read_text(encoding='utf-8')) != draft:
                    raise ValueError('The saved project map changed while editing. Review it again before saving.')
                draft['blueprint'] = value['blueprint']
                draft['summaries'] = summaries
                draft['unreviewed'] = sorted(set(draft.get('included', draft['source_sha256'])) - set(summaries))
                from .workspace import write_json
                write_json(self.ws.project(draft['project']) / 'initialization-draft.json', draft)
                self.show_initialization(draft)
                self.update_gate()
                dialog.destroy()
            self.guard(perform)
        ttk.Button(dialog, text='Save draft', command=save).pack(pady=5)

    def run_mission(self):
        if not self.project or not self.revision or self.busy:
            return
        def start():
            require_ready(self.ws, self.project, self.revision)
            project, revision = self.project, self.revision
            mission = self.goal.get('1.0', 'end').strip()
            endpoint, model = self.endpoint.get().strip(), self.model.get().strip()
            self.save_model()
            if not mission or not model:
                raise ValueError('Enter a mission and choose a model.')
            check = self.check_command.get().strip() or None
            diagnostic = diagnostic_for_mission(self.ws, project, revision) if self.include_run.get() else None
            self.pending_mission = None
            self.busy = True
            self.update_gate()
            self.activity('Mission started. Waiting for progress from the controller.', clear=True)
            self.activity_indicator.start(12)
            self.status.set('Mission starting…')
            def worker():
                try:
                    result = prepare_mission(self.ws, project, revision, mission, endpoint, model, check,
                                             lambda phase: self.events.put(('automation_progress', phase)), diagnostic)
                    self.events.put(('mission_review', result))
                except Exception as exc:
                    self.events.put(('automation_error', str(exc)))
            threading.Thread(target=worker, daemon=True).start()
        self.guard(start)

    def run_diagnosis(self):
        if not self.project or not self.revision or self.busy:
            return
        def start():
            require_ready(self.ws, self.project, self.revision)
            project, revision = self.project, self.revision
            mission = self.goal.get('1.0', 'end').strip()
            endpoint, model = self.endpoint.get().strip(), self.model.get().strip()
            self.save_model()
            if not mission or not model:
                raise ValueError('Enter a diagnosis request and choose a model.')
            selected = self.selected_files()
            diagnostic = diagnostic_for_mission(self.ws, project, revision) if self.include_run.get() else None
            self.busy = True
            self.update_gate()
            self.activity('Diagnosis started; no source edits will be made.', clear=True)
            self.activity_indicator.start(12)
            self.status.set('Diagnosis starting…')
            def worker():
                try:
                    result = diagnose_mission(self.ws, project, revision, mission, endpoint, model,
                                              selected, diagnostic, lambda phase: self.events.put(('automation_progress', phase)))
                    self.events.put(('diagnosis_complete', result))
                except Exception as exc:
                    self.events.put(('automation_error', str(exc)))
            threading.Thread(target=worker, daemon=True).start()
        self.guard(start)

    def accept_automated_mission(self):
        if not self.pending_mission or self.busy:
            return
        def perform():
            project = self.pending_mission['project']
            if (project, self.pending_mission.get('final_revision')) != (self.project, self.revision):
                raise ValueError('Select the mission candidate revision before accepting it.')
            revision = accept_mission(self.ws, project, self.pending_mission)
            self.pending_mission = None
            self.refresh(project, revision)
            self.status.set('Mission accepted as the default revision.')
            self.update_gate()
        self.guard(perform)

    def preview(self, _=None):
        if not self.revision:
            return
        selected = self.selected_files()
        if not selected:
            return
        root = self.ws.revision(self.project, self.revision)
        try:
            self.show('Source', read_source(root, selected[-1]))
        except (ValueError, UnicodeError) as exc:
            self.show('Source', str(exc))
        shadows = json.loads((root / '.hornet-meta' / 'shadows.json').read_text())
        self.show('Shadow', json.dumps(shadows.get(selected[-1], {}), indent=2))

    def selected_files(self):
        return [self.files.item(i, 'values')[0] for i in self.files.selection() if self.files.item(i, 'values')]

    def revision_context_menu(self, event):
        if self.busy:
            return
        row = self.projects.identify_row(event.y)
        if not row:
            return
        values = self.projects.item(row, 'values')
        if len(values) != 2:
            return
        project, revision = values
        self.projects.selection_set(row)
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='Rename revision…', command=lambda: self.rename_revision(project, revision))
        menu.add_command(label='Save revision to disk…',
                         command=lambda: self.export_revision(project, revision))
        try:
            ready = require_ready(self.ws, project)
            can_trash = not self.busy and ready.get('accepted_revision') != revision
        except ValueError:
            can_trash = False
        menu.add_command(label='Move revision to trash…',
                         command=lambda: self.trash_revision(project, revision),
                         state='normal' if can_trash else 'disabled')
        menu.tk_popup(event.x_root, event.y_root)

    def rename_revision(self, project, revision):
        def perform():
            records = ensure_display(self.ws, project)
            name = simpledialog.askstring('Rename revision',
                                          'Revision name (leave blank to remove the name):',
                                          initialvalue=records[revision].get('name', ''), parent=self.root)
            if name is None:
                return
            rename_revision(self.ws, project, revision, name)
            self.refresh(project, revision)
            self.status.set('Revision name saved. Its ID, color and accepted status are unchanged.')
        self.guard(perform)

    def export_revision(self, project, revision):
        parent = filedialog.askdirectory(title='Choose where to save the complete revision', parent=self.root)
        if not parent:
            return
        def perform():
            destination = self.ws.export_revision(project, revision, parent)
            self.status.set('Revision saved: ' + str(destination))
            messagebox.showinfo('Revision saved', 'Saved the complete revision to:\n' + str(destination), parent=self.root)
        self.guard(perform)

    def copy_revision(self):
        if self.revision:
            def copy():
                require_ready(self.ws, self.project, self.revision)
                self.refresh(self.project, self.ws.fork(self.project, self.revision))
            self.guard(copy)

    def delete_project(self):
        if not self.project or self.busy:
            return
        project = self.project
        if not messagebox.askyesno(
                'Delete project',
                f'Move the entire project “{project}” to project trash?\n\n'
                'All its revisions, pending work and records will leave the project list. '
                'They can be recovered from the project-trash folder. '
                'The original imported folder is not changed.', parent=self.root):
            return
        def perform():
            destination = self.ws.trash_project(project)
            self.project = self.revision = None
            self.pending = self.pending_initialization = self.pending_mission = None
            self.refresh()
            self.files.delete(*self.files.get_children())
            for name in self.views:
                self.show(name, '')
            self.update_gate()
            self.status.set(f'Project moved to trash: {destination}')
        self.guard(perform)

    def trash_revision(self, project=None, revision=None):
        project = project or self.project
        revision = revision or self.revision
        if not project or not revision or self.busy:
            return
        def trash():
            state = require_ready(self.ws, project)
            if state.get('accepted_revision') == revision:
                raise ValueError('The accepted revision cannot be moved to trash. Accept another revision first.')
            if not messagebox.askyesno('Move revision to trash',
                                       f'Move {project} / {revision} to the project trash folder?\n\n'
                                       'Files remain recoverable on disk.', parent=self.root):
                return
            state = require_ready(self.ws, project)
            if state.get('accepted_revision') == revision:
                raise ValueError('The accepted revision cannot be moved to trash. Accept another revision first.')
            self.ws.trash(project, revision)
            if (self.pending_mission and self.pending_mission.get('project') == project
                    and self.pending_mission.get('final_revision') == revision):
                self.pending_mission = None
                self.show('Proposal', '')
            self.refresh(project, self.ws.revisions(project)[0])
            self.update_gate()
            self.status.set('Revision moved to trash; its files are recoverable.')
        self.guard(trash)

    def edit_blueprint(self):
        if not self.revision:
            return
        project, revision = self.project, self.revision
        root = self.ws.revision(project, revision)
        dialog = tk.Toplevel(self.root)
        dialog.title('Project blueprint · JSON')
        editor = tk.Text(dialog, width=90, height=24)
        editor.pack(fill='both', expand=True)
        editor.insert('1.0', (root / '.blueprint').read_text())
        def save():
            def perform():
                data = json.loads(editor.get('1.0', 'end'))
                if not isinstance(data, dict) or not data:
                    raise ValueError('Blueprint must be a nonempty JSON object.')
                rev = self.ws.fork(project, revision, mission='Update project blueprint', blueprint=data)
                self.refresh(project, rev)
                dialog.destroy()
            self.guard(perform)
        ttk.Button(dialog, text='Save as new revision', command=save).pack(pady=5)

    def prepare(self):
        messagebox.showerror('Hornet', 'Use Run mission so source edits go through the atomic editor.')

    def run_project(self):
        if not self.project or not self.revision or self.busy:
            return
        try:
            require_ready(self.ws, self.project, self.revision)
        except ValueError as exc:
            messagebox.showerror('Run in Test VM', str(exc))
            return
        command = simpledialog.askstring('Run in Test VM', 'Python command to run inside the Test VM:',
                                         initialvalue='python3 -m app', parent=self.root)
        if not command:
            return
        project, revision = self.project, self.revision
        self.busy = True
        self.update_gate()
        self.status.set('Running a copy in the configured test VM…')
        def worker():
            try:
                self.events.put(('run', run_python(self.ws, project, revision, command)))
            except Exception as exc:
                self.events.put(('run_error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def load_models(self):
        endpoint = self.endpoint.get().strip()
        self.find_models.configure(state='disabled')
        self.status.set('Finding models at the configured server…')
        def worker():
            try:
                self.events.put(('models', (endpoint, discover_models(endpoint))))
            except Exception as exc:
                self.events.put(('models_error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def close(self):
        if self.busy and not messagebox.askyesno(
                'Close Mission Control',
                'Work is still running. Close and interrupt it?\n\n'
                'Saved initialization progress and revisions remain on disk.', parent=self.root):
            return
        self.root.destroy()

    def poll(self):
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            if kind == 'automation_progress':
                self.activity(value)
                self.status.set(value + '…')
                self.root.after(100, self.poll)
                return
            if kind == 'automation_error':
                self.activity_indicator.stop()
                self.activity('Stopped: ' + value)
                self.busy = False
                if self.project:
                    draft_path = self.ws.project(self.project) / 'initialization-draft.json'
                    if draft_path.is_file():
                        self.pending_initialization = json.loads(draft_path.read_text(encoding='utf-8'))
                self.status.set('Automation stopped. Saved initialization progress can be retried; the accepted revision is unchanged.')
                self.update_gate()
                messagebox.showerror('Hornet automation', value)
                self.root.after(100, self.poll)
                return
            if kind == 'initialized_draft':
                self.activity_indicator.stop()
                self.activity('Project map draft saved. Review its completeness.')
                self.busy = False
                self.pending_initialization = value
                self.show_initialization(value)
                self.update_gate()
                self.root.after(100, self.poll)
                return
            if kind == 'diagnosis_complete':
                self.activity_indicator.stop()
                self.activity('Diagnosis ready for review.')
                self.busy = False
                self.show_diagnosis(value)
                self.update_gate()
                self.root.after(100, self.poll)
                return
            if kind == 'mission_review':
                self.activity_indicator.stop()
                self.activity('Mission ready for review.')
                self.busy = False
                self.pending_mission = value
                if self.project == value['project']:
                    self.refresh(value['project'], value['final_revision'])
                self.show_mission(value)
                self.update_gate()
                self.root.after(100, self.poll)
                return
            if kind == 'run_error':
                self.busy = False
                self.update_gate()
                self.status.set('Test VM run failed to start.')
                messagebox.showerror('Test VM run', value)
                self.root.after(100, self.poll)
                return
            if kind == 'run':
                self.busy = False
                self.update_gate()
                hint = run_hint(value)
                self.show('Run output', f"Project: {value['project']}\nRevision: {value['revision']}\nCommand: {shlex.join(value['command'])}\nExit code: {value['exit_code']}\nTimed out: {value['timed_out']}\n\n{('Environment hint: ' + hint + chr(10) + chr(10)) if hint else ''}--- stdout ---\n{value['stdout']}\n--- stderr ---\n{value['stderr']}")
                self.tabs.select(4)
                self.status.set('VM run saved. Select that revision and include run output in a repair mission if needed.')
                self.root.after(100, self.poll)
                return
            if kind == 'models_error':
                self.find_models.configure(state='normal')
                self.status.set('Could not find models. Check the server URL and try again.')
                messagebox.showerror('Find models', value)
                self.root.after(100, self.poll)
                return
            if kind == 'models':
                self.find_models.configure(state='normal')
                endpoint, models = value
                if endpoint == self.endpoint.get().strip():
                    self.models.configure(values=models)
                    self.status.set(f'Found {len(models)} models. Choose the model you want to use.')
                self.root.after(100, self.poll)
                return
            self.busy = False
            self.send.configure(state='normal')
            if kind == 'error':
                self.status.set('Proposal failed; no revision changed.')
                messagebox.showerror('LM Studio', value)
            else:
                context, proposal = value
                self.pending = value
                diff = [f'Project: {context[0]}\nBase revision: {context[1]}\n\n{proposal["summary"]}\n']
                for name, content in proposal['files'].items():
                    previous = context[3]['files'].get(name)
                    diff.append(f'\n{"New file" if previous is None else "Changed file"}: {name}\n')
                    diff.extend(difflib.unified_diff(previous.splitlines(True) if previous is not None else [], content.splitlines(True), fromfile=name + ' (before)' if previous is not None else '/dev/null', tofile=name + ' (proposed)'))
                self.show('Proposal', ''.join(diff))
                self.tabs.select(3)
                self.update_gate()
                self.status.set('Review proposal. Applying creates a new revision of the displayed base.')
        self.root.after(100, self.poll)

    def apply(self):
        messagebox.showerror('Hornet', 'Use Run mission so source edits go through the atomic editor.')


def main():
    parser = argparse.ArgumentParser(description='Hornet native desktop mission control')
    parser.add_argument('--workspace', help='Workspace directory (default: ~/.hornet)')
    parser.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = tk.Tk()
    App(root, Workspace(args.workspace))
    if args.smoke_test:
        root.after(600, root.destroy)
    root.mainloop()
