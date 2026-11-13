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
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog, font as tkfont

from .workspace import Workspace, source_files, read_source, request_proposal, discover_models, write_json
from .layouts import LAYOUTS
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
        self.cancel_token = None
        self.operation_started = None
        self.operation_phase = "Working"
        self.events = queue.Queue()
        self.revision_icons = {}
        from .response_chime import ResponseChime
        self.response_chime = ResponseChime()
        from .shell import build_shell
        build_shell(self)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()
        self.update_gate()
        root.after(100, self.poll)

    def begin_query(self):
        from .cancellation import Cancellation
        self.cancel_token = Cancellation()
        self.operation_started = time.monotonic()
        self.operation_phase = 'Starting'
        self.busy = True

    def stop_query(self):
        if not self.busy or self.cancel_token is None:
            return
        self.cancel_token.cancel()
        self.operation_phase = 'Stopping at a safe boundary; discarding any late model response'
        self.status.set(self.operation_phase + '. The model server may still finish its request.')
        self.activity(self.status.get())
        self.update_gate()

    def launch_query(self, worker):
        from .cancellation import bind
        token = self.cancel_token
        def run():
            bind(token)
            try:
                worker()
            finally:
                bind(None)
        threading.Thread(target=run, daemon=True).start()

    def open_projects_folder(self):
        def perform():
            import os
            import sys
            import subprocess
            folder = str(self.ws.root)
            if sys.platform == 'win32':
                os.startfile(folder)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
        self.guard(perform)

    def set_projects_folder(self):
        if self.busy:
            return
        selected = filedialog.askdirectory(parent=self.root, initialdir=str(self.ws.root),
                                            title='Choose Hornet Coder workspace (contains projects)')
        if not selected:
            return
        def perform():
            from .workspace_settings import validate_root, save_root
            folder = validate_root(selected)
            if not messagebox.askyesno('Set Hornet Coder folder',
                    'Use this folder after restarting Coder?\n\n' + str(folder) +
                    '\n\nProjects will be stored in its projects subfolder. Existing projects stay where they are; '
                    'nothing is moved or deleted. This session stays in the current folder. '
                    'A --workspace launch option overrides this saved setting.', parent=self.root):
                return
            save_root(folder)
            self.status.set('Folder saved for next launch: ' + str(folder) + '. Restart Coder when ready; this session is unchanged.')
            messagebox.showinfo('Folder saved', 'Restart Coder when ready to use:\n' + str(folder) +
                                '\n\nYour existing projects have not moved.', parent=self.root)
        self.guard(perform)

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

    def load_response_sound(self):
        from .response_chime import load_enabled
        return load_enabled(self.ws.root / 'desktop-settings.json')

    def save_response_sound(self):
        from .response_chime import save_enabled
        try:
            save_enabled(self.ws.root / 'desktop-settings.json', self.response_sound.get())
        except (OSError, ValueError):
            self.status.set('Sound changed for this session, but could not save the setting.')

    def notify_response(self):
        # A notification must never prevent the response from being displayed.
        if self.response_sound.get():
            self.response_chime.play()

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

    def revision_icon(self, color, parent_color, locked=False):
        key = (color, parent_color, locked)
        if key not in self.revision_icons:
            icon = tk.PhotoImage(master=self.root, width=46 if locked else 32, height=14)
            for center, fill in ((7, color), (24, parent_color)):
                if not fill:
                    continue
                for y in range(1, 14):
                    for x in range(center-6, center+7):
                        distance = (x-center)**2 + (y-7)**2
                        if distance <= 36:
                            icon.put('#202020' if distance >= 25 else fill, (x, y))
            if locked:
                # A crisp padlock independent of platform emoji/font support.
                for x, y in ((x, y) for x in range(36, 43) for y in range(2, 8)):
                    if y == 2 or x in (36, 42):
                        icon.put('#c7d2df', (x, y))
                icon.put('#c7d2df', to=(34, 7, 45, 14))
                icon.put('#18212d', to=(39, 9, 41, 12))
            self.revision_icons[key] = icon
        return self.revision_icons[key]

    def refresh(self, project=None, revision=None):
        from .working_revision import refresh_working_revision, load_working
        if isinstance(self.ws, Workspace) and not self.busy:
            for name in self.ws.list_projects():
                try:
                    head = refresh_working_revision(self.ws, name)
                    state = load_working(self.ws, name)
                    anchor = getattr(self, '_working_selection_anchor', None)
                    if (name == project and head and state and
                            (revision == state.get('current') or
                             (anchor == (name, state.get('current')) and revision == self.revision))):
                        revision = head
                except (OSError, ValueError) as exc:
                    self.status.set('Working revision could not refresh: ' + str(exc))
        self.projects.delete(*self.projects.get_children())
        self.revision_icons.clear()
        for name in self.ws.list_projects():
            item = self.projects.insert('', 'end', text=name, values=(name,), open=True)
            display = ensure_display(self.ws, name)
            from .working_revision import visible_revisions, is_working, display_revision_id
            for rev in visible_revisions(self.ws, name, getattr(self, 'show_checkpoints', False)):
                parent = revision_parent(self.ws, name, rev)
                own_id = display_revision_id(self.ws, name, rev).rsplit('-', 1)[-1]
                parent_id = parent.rsplit('-', 1)[-1] if parent else 'root'
                custom_name = display[rev].get('name', '')
                locked = not is_working(self.ws, name, rev)
                working_label = '' if locked else '[WORKING]  '
                label = (working_label + own_id + ' ← ' + parent_id
                         + '  ' + (custom_name or rev))
                if custom_name:
                    label += '  (' + rev + ')'
                icon = self.revision_icon(display[rev]['color'], display[parent]['color'] if parent else None, locked=locked)
                row = self.projects.insert(item, 'end', text=label, image=icon, values=(name, rev))
                if (name, rev) == (project, revision):
                    self.projects.selection_set(row)
                    self.projects.see(row)

    def new_calculator_example(self):
        if self.busy:
            return
        name = simpledialog.askstring('Calculator example', 'Project name:',
                                      initialvalue='CalculatorExample', parent=self.root)
        if not name:
            return
        def create():
            revision = self.ws.create(name, 'calculator-example')
            self.refresh(name, revision)
            self.status.set('Calculator example created. Select Understand project to review it.')
        self.guard(create)

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
                    self.status.set('Project imported. Select Understand project to review what it does.')
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
        from .file_picker import FilePicker
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.geometry('850x650')
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=explanation, wraplength=780, justify='left').pack(anchor='w', pady=(0, 8))
        picker = FilePicker(frame, names, selected)
        picker.pack(fill='both', expand=True, pady=8)
        controls = ttk.Frame(frame)
        controls.pack(fill='x')
        def proceed(resume):
            picked = picker.selected_files()
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
        from .selection_state import remember, restore
        previous = (self.project, self.revision)
        remember(self)
        self.project = values[0]
        self.revision = values[1] if len(values) == 2 else None
        if self.revision is None:
            state = read_state(self.ws, self.project)
            default = state.get('accepted_revision') or next(iter(self.ws.revisions(self.project)), None)
            if default:
                for child in self.projects.get_children(selected[0]):
                    if tuple(self.projects.item(child, 'values')) == (self.project, default):
                        self.project, self.revision = previous
                        self.projects.selection_set(child)
                        return
        from .working_revision import is_working, display_revision_id
        self._working_selection_anchor = ((self.project, display_revision_id(self.ws, self.project, self.revision))
            if self.revision and is_working(self.ws, self.project, self.revision) else None)
        self.pending_mission = None
        self.pending_initialization = None
        self.show('Proposal', '')
        self.show('Run output', '')
        self.show('Activity', '')
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
        self._prompt_paths = {}
        self._prompt_paths_context = (self.project, self.revision)
        for key in ('Source', 'Shadow', 'Blueprint'):
            self.show(key, '')
        if not self.revision:
            restore(self)
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
                    self._prompt_paths[folders[key]] = key
            row = self.files.insert(folders.get(path.parent.as_posix(), ''), 'end', text=path.name, values=(name,))
            self._prompt_paths[row] = name
        from .understanding import describe
        self.show('Blueprint', describe(json.loads((root / '.blueprint').read_text(encoding='utf-8'))))
        try:
            require_ready(self.ws, self.project, self.revision)
        except ValueError as exc:
            self.status.set(str(exc))
        else:
            self.status.set(f'{self.project} / {self.revision} · Select files for diagnosis; missions use model-planned scope.')

        if (self.pending_initialization and
                (self.pending_initialization.get('project'), self.pending_initialization.get('revision')) ==
                (self.project, self.revision)):
            from .understanding import draft_status, review_text
            text = draft_status(self.pending_initialization)
            if read_state(self.ws, self.project).get('status') == 'ready':
                text += ' Previously accepted understanding remains available; this refresh is not accepted.'
            self.status.set(text)
            self.show('Proposal', review_text(self.pending_initialization))
            self.show('Blueprint', review_text(self.pending_initialization))
        restore(self)
        self.update_gate()

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
        from .working_revision import is_working
        working = selected and is_working(self.ws, self.project, self.revision)
        self.auto_button.configure(state='normal' if revision_ready and working and not self.busy else 'disabled')
        self.delete_project_button.configure(state='normal' if self.project and not self.busy else 'disabled')
        if hasattr(self, 'new_project_button'):
            self.new_project_button.configure(state='disabled' if self.busy else 'normal')
            self.import_button.configure(state='disabled' if self.busy else 'normal')
            self.revision_menu_button.configure(state='normal' if selected and not self.busy else 'disabled')
            self.review_button.configure(state='normal' if self.project and not self.busy else 'disabled')
        if hasattr(self, 'init_button'):
            self.init_button.configure(state='normal' if selected and (not project_ready or revision_ready) and not self.busy else 'disabled')
        from .understanding import draft_complete
        draft_ready = (self.pending_initialization and draft_complete(self.pending_initialization)
                       and self.pending_initialization.get('project') == self.project
                       and self.pending_initialization.get('revision') == self.revision)
        self.accept_map.configure(state='normal' if draft_ready and not self.busy else 'disabled')
        self.edit_map.configure(state='normal' if self.pending_initialization and self.pending_initialization.get('project') == self.project and self.pending_initialization.get('revision') == self.revision and not self.busy else 'disabled')
        can_accept = (self.pending_mission and self.pending_mission.get('project') == self.project
                      and self.pending_mission.get('final_revision') == self.revision
                      and self.pending_mission.get('state') == 'review'
                      and (not self.pending_mission.get('check_command') or self.pending_mission.get('checks_passed')))
        self.accept_mission_button.configure(state='normal' if can_accept and not self.busy else 'disabled')
        if hasattr(self, 'workflow_hint'):
            from .shell import sync_shell
            sync_shell(self, project_ready, revision_ready)

    def show_initialization(self, value):
        from .understanding import review_text, draft_complete
        self.show('Proposal', review_text(value))
        self.tabs.select(3)
        if (value.get('project'), value.get('revision')) != (self.project, self.revision):
            self.status.set('Select the project map’s base revision before editing or accepting it.')
        elif value.get('version') != 2:
            self.status.set('This older draft needs to be initialized again before acceptance.')
        elif not draft_complete(value):
            self.status.set('Understanding incomplete. Select Understand project to retry missing parts.')
        else:
            self.status.set('Review the understanding, then choose Looks right, continue to accept it.')

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
                  'syntax_checks': value.get('syntax_checks'),
                  'tool_receipts': value.get('tool_receipts', [])}
        if value.get('proposed_goals'):
            report['agent_subgoals'] = value['proposed_goals']
            report['goal_evidence'] = value.get('goal_evidence', [])
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
        from .syntax_checks import summary as syntax_summary
        summary = ('MISSION REVIEW\n' + syntax_summary(value.get('syntax_checks')) + '\nExtra VM check: ' + check_summary +
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
            self.status.set(syntax_summary(value.get('syntax_checks')))
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
                from .working_revision import is_abandoned
                records = [value for value in records if not is_abandoned(self.ws, self.project, value.get('id'))]
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
                if interrupted and interrupted.get('counter_plan_of') and interrupted.get('state') != 'plan_review':
                    previous = next((value for value in records if value.get('id') == interrupted['counter_plan_of']
                                     and value.get('project') == self.project and value.get('state') == 'plan_review'), None)
                    if previous is not None:
                        interrupted = previous
                if interrupted and interrupted.get('state') == 'plan_review':
                    self.show_mission_plan(interrupted)
                    self.update_gate()
                    return
                if interrupted:
                    self.pending_mission = None
                    from .attention import review_text
                    heading = review_text(self.ws, self.project, interrupted) if interrupted.get('state') in ('failed', 'cancelled') else ''
                    self.show('Proposal', heading + json.dumps(interrupted, indent=2))
                    self.tabs.select(3)
                    self.status.set('This mission stopped or was interrupted. Inspect the record; use Project actions > Resume interrupted mission to continue. The accepted revision is unchanged.')
                    self.update_gate()
                    return
            raise ValueError('There is no pending project map or mission to review.')
        self.guard(perform)

    def initialize_project(self):
        if not self.project or not self.revision or self.busy:
            return
        project, revision = self.project, self.revision
        endpoint, model = self.endpoint.get().strip(), self.model.get().strip()
        if not model:
            messagebox.showerror('Understand project', 'Choose a model first.')
            return
        previous = self.pending_initialization
        if not previous:
            draft_path = self.ws.project(project) / 'initialization-draft.json'
            if draft_path.is_file():
                previous = json.loads(draft_path.read_text(encoding='utf-8'))
        previous_selection = (previous.get('included') if previous and previous.get('version') == 2
                              and previous.get('project') == project and previous.get('revision') == revision else None)
        def begin(selected, resume):
            self.begin_query()
            self.pending_initialization = None
            self.update_gate()
            self.activity('Understanding project.', clear=True)
            self.activity_indicator.start(12)
            self.status.set('Understanding project; progress is saved after each part.')
            def worker():
                try:
                    draft = make_initialization_draft(self.ws, project, revision, model_describer(endpoint, model),
                                                      lambda phase: self.events.put(('automation_progress', phase)),
                                                      included=selected, resume=resume)
                    self.events.put(('initialized_draft', draft))
                except Exception as exc:
                    self.events.put(('automation_error', str(exc)))
            self.launch_query(worker)
        self.choose_project_files(self.ws.revision(project, revision), 'Understand project · choose files',
                                  'The selected files and current project understanding will be sent to the model server at '
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
            self.status.set('Project understanding accepted. Describe a change or run the project.')
            self.update_gate()
        self.guard(perform)

    def edit_project_map(self):
        if (not self.pending_initialization or self.busy or
                (self.pending_initialization.get('project'), self.pending_initialization.get('revision')) != (self.project, self.revision)):
            return
        draft = self.pending_initialization
        dialog = tk.Toplevel(self.root)
        dialog.title('Correct understanding · JSON')
        from .understanding import UnderstandingEditor
        editor = UnderstandingEditor(dialog, draft['blueprint'], draft['summaries'])
        editor.pack(fill='both', expand=True)
        dialog.title('Correct understanding')
        def save():
            def perform():
                if self.busy or (self.project, self.revision) != (draft['project'], draft['revision']) or self.pending_initialization is not draft:
                    raise ValueError('The selected project or pending map changed. Review it again before saving.')
                value = editor.value()
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
            from .working_revision import require_working
            require_working(self.ws, self.project, self.revision)
            project, revision = self.project, self.revision
            mission = self.goal.get('1.0', 'end').strip()
            endpoint, model = self.endpoint.get().strip(), self.model.get().strip()
            self.save_model()
            if not mission or not model:
                raise ValueError('Enter a mission and choose a model.')
            from .working_revision import pending_mission_record, abandon_working_revision
            pending = pending_mission_record(self.ws, project, revision)
            if pending:
                if not messagebox.askyesno('Keep partial work and start a new mission?',
                        'This working revision has an unfinished mission. Keep its current partial files and start a new mission?'
                        '\n\nThe original mission will no longer resume automatically. Its saved mission and checkpoints, including unresolved check outcomes, remain in recovery history.'
                        '\n\nChoose No to keep the original mission available for Resume interrupted mission.', parent=self.root):
                    return
                abandon_working_revision(self.ws, project, revision)
            check = self.check_command.get().strip() or None
            diagnostic = diagnostic_for_mission(self.ws, project, revision) if self.include_run.get() else None
            self.pending_mission = None
            confirm_plan = self.confirm_plan.get()
            self.begin_query()
            self.update_gate()
            self.activity('Mission started. Waiting for progress from the controller.', clear=True)
            self.activity_indicator.start(12)
            self.status.set('Mission starting…')
            def worker():
                try:
                    result = prepare_mission(self.ws, project, revision, mission, endpoint, model, check,
                                             lambda phase: self.events.put(('automation_progress', phase)), diagnostic,
                                             require_plan_confirmation=confirm_plan, require_goal_decomposition=True)
                    self.events.put(('plan_review' if result.get('state') == 'plan_review' else 'mission_review', result))
                except Exception as exc:
                    self.events.put(('automation_error', str(exc)))
            self.launch_query(worker)
        self.guard(start)

    def show_mission_plan(self, value):
        self.pending_mission = value
        lines = ['REVIEW AGENT PLAN', value['mission'],
                 'The agent has proposed these subgoals. No source edits have run yet.']
        references = value.get('plan', {}).get('context_files', [])
        if references:
            lines.append('Read-only context (not editable): ' + ', '.join(references))
        for node in value.get('proposed_goals', []):
            lines.append(node['id'] + ' | ' + node['goal'] +
                         '\nParent: ' + str(node.get('parent') or 'Original goal') +
                         '\nFiles: ' + ', '.join(node.get('files', [])) +
                         '\nSuccess evidence: ' + node.get('success_criteria', '') +
                         '\nDepends on: ' + (', '.join(node.get('depends_on', [])) or 'None'))
        lines.append('Counter plan to request changes without executing edits, or Approve plan & continue to execute this exact saved plan.')
        self.show('Proposal', '\n\n'.join(lines))
        self.tabs.select(3)
        self.status.set('Agent plan ready for review. No edits performed. Approval applies only to this saved plan.')

    def selected_mission_base(self):
        """Resolve an explicitly adopted legacy mission without editing its base."""
        from .working_revision import is_working, resume_base
        if self.project and self.revision and is_working(self.ws, self.project, self.revision):
            return resume_base(self.ws, self.project, self.revision)
        return self.revision

    def counter_saved_plan(self):
        value = self.pending_mission
        if (self.busy or not value or value.get('state') != 'plan_review' or
                (value.get('project'), value.get('base_revision')) != (self.project, self.selected_mission_base())):
            return
        from .plan_counter import feedback_path, save_feedback, counter_mission_plan
        dialog = tk.Toplevel(self.root)
        dialog.title('Counter agent plan')
        ttk.Label(dialog, text='What should change? A revised plan still requires your explicit approval.').pack(padx=12, pady=12)
        feedback = tk.Text(dialog, width=75, height=10, wrap='word')
        feedback.pack(fill='both', expand=True, padx=12)
        path = feedback_path(self.ws, value)
        if path.exists():
            feedback.insert('1.0', json.loads(path.read_text(encoding='utf-8')).get('feedback', ''))
        def submit():
            def start():
                if self.busy or self.pending_mission is not value or (self.project, self.selected_mission_base()) != (value['project'], value['base_revision']):
                    raise ValueError('The selected plan changed. Review it again before countering.')
                from .working_revision import require_working
                require_working(self.ws, self.project, self.revision)
                text = save_feedback(self.ws, value, feedback.get('1.0', 'end'))
                self.begin_query()
                self.update_gate()
                self.activity('Requesting a revised plan. No execution approved.', clear=True)
                self.status.set('Revising plan only. Your pending plan and feedback are saved.')
                dialog.destroy()
                def worker():
                    try:
                        result = counter_mission_plan(self.ws, value, text,
                            lambda phase: self.events.put(('automation_progress', phase)))
                        self.events.put(('plan_review', result))
                    except Exception as exc:
                        self.events.put(('automation_error', 'Plan revision stopped. Your previous plan and counter feedback are saved; use Counter plan to retry. ' + str(exc)))
                self.launch_query(worker)
            self.guard(start)
        ttk.Button(dialog, text='Request revised plan', command=submit).pack(side='right', padx=12, pady=12)
        ttk.Button(dialog, text='Cancel', command=dialog.destroy).pack(side='right', pady=12)
        feedback.focus_set()

    def approve_saved_plan(self):
        value = self.pending_mission
        if (self.busy or not value or value.get('state') != 'plan_review' or
                (value.get('project'), value.get('base_revision')) != (self.project, self.selected_mission_base())):
            return
        def start():
            from .working_revision import require_working
            require_working(self.ws, self.project, self.revision)
            from .mission_resume import approve_mission_plan, resume_mission
            project, identifier = self.project, value['id']
            endpoint, model = self.endpoint.get().strip(), self.model.get().strip()
            saved = json.loads((self.ws.project(project) / 'missions' / (identifier + '.json')).read_text(encoding='utf-8'))
            if saved != value:
                raise ValueError('The saved plan changed since review. Review it again before approving.')
            approve_mission_plan(self.ws, project, identifier)
            # Approval changes the saved record. Retire the unapproved UI snapshot
            # even if continuation fails; retry through verified mission resume.
            self.pending_mission = None
            self.begin_query()
            self.update_gate()
            self.activity('Approved saved plan. Continuing from verified checkpoints.', clear=True)
            def worker():
                try:
                    result = resume_mission(self.ws, project, identifier, endpoint, model,
                                            lambda phase: self.events.put(('automation_progress', phase)))
                    self.events.put(('plan_review' if result.get('state') == 'plan_review' else 'mission_review', result))
                except Exception as exc:
                    self.events.put(('automation_error', str(exc)))
            self.launch_query(worker)
        self.guard(start)

    def resume_saved_mission(self, expected_id=None):
        if self.busy or not self.project or not self.revision:
            return
        def start():
            from .working_revision import require_working, is_abandoned
            require_working(self.ws, self.project, self.revision)
            directory = self.ws.project(self.project) / 'missions'
            records = [json.loads(path.read_text(encoding='utf-8')) for path in
                       sorted(directory.glob('*.json'), key=lambda path: path.stat().st_mtime, reverse=True)]
            saved = next((record for record in records if record.get('base_revision') == self.selected_mission_base()
                          and record.get('project') == self.project
                          and (expected_id is None or record.get('id') == expected_id)
                          and record.get('resumable') and record.get('state') not in ('accepted', 'review')
                          and not is_abandoned(self.ws, self.project, record.get('id'))), None)
            if saved is None:
                raise ValueError('No resumable interrupted mission belongs to this base revision. Select its working revision, or create a recovery working revision from the original base and review pending work.')
            if not messagebox.askyesno('Resume mission', 'Resume this saved goal from verified checkpoints?\n\n' +
                    saved['mission'] + '\n\nUncertain edits or test effects will stop for reconciliation, not be repeated.', parent=self.root):
                return
            endpoint, model = self.endpoint.get().strip(), self.model.get().strip()
            if not model:
                raise ValueError('Choose the model used for this mission first.')
            project, identifier = self.project, saved['id']
            self.begin_query()
            self.update_gate()
            self.activity('Resuming saved mission from verified checkpoints.', clear=True)
            def worker():
                try:
                    from .mission_resume import resume_mission
                    result = resume_mission(self.ws, project, identifier, endpoint, model,
                                            lambda phase: self.events.put(('automation_progress', phase)))
                    self.events.put(('plan_review' if result.get('state') == 'plan_review' else 'mission_review', result))
                except Exception as exc:
                    self.events.put(('automation_error', str(exc)))
            self.launch_query(worker)
        self.guard(start)

    def attention_action(self, action):
        from .attention import current
        info = current(self)
        if not info:
            return
        if action == 'resume' and info['can_resume']:
            self.resume_saved_mission(expected_id=info['record']['id'])
        elif action == 'new':
            self.goal.focus_set()
            self.goal.tag_add('sel', '1.0', 'end-1c')
            self.status.set('Describe the new mission, then choose Run mission. You will confirm keeping partial work before the old mission is closed.')
        elif action == 'discard':
            self.discard_working_revision()

    def show_goal_pointers(self):
        if not self.project or not self.revision:
            return
        def show():
            from .revision_goals import read_revision_goals
            root = self.ws.revision(self.project, self.revision)
            value = read_revision_goals(root)
            lines = ['REVISION GOAL', value.get('goal', value.get('note', 'No goal snapshot.')),
                     'Revision: ' + self.revision, 'Parent: ' + str(value.get('parent_revision') or 'None'),
                     'Snapshot pointer: ' + str(value.get('current', 'Unavailable')),
                     'Snapshots describe publication time. Acceptance and active checkpoints are separate.']
            accepted = read_state(self.ws, self.project).get('accepted_revision')
            lines.append('Current acceptance: ' + ('Explicitly accepted as the current default revision.' if accepted == self.revision
                         else 'Not the current accepted default; publication alone does not imply acceptance.'))
            for path, node in value.get('goals', {}).items():
                lines.append(path + ': ' + node['status'] + ' | ' + node['label'])
            snapshot = value.get('operation_snapshot') or {}
            for node in snapshot.get('proposed_goals', []):
                lines.append('Agent goal ' + node['id'] + ': ' + node['goal'] +
                             '\nParent: ' + str(node.get('parent')) + '\nEvidence criterion: ' + node['success_criteria'])
            working = self.ws.project(self.project) / 'working_memory'
            for path in sorted(working.glob('understanding/*/state.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
                if path.is_symlink():
                    continue
                state = json.loads(path.read_text(encoding='utf-8'))
                if state.get('identity', {}).get('revision') != self.revision:
                    continue
                lines.append('Understanding checkpoint: ' + json.dumps(state.get('pointer')))
                lines.append('Journal sequence: ' + str(state.get('sequence')))
                break
            missions = self.ws.project(self.project) / 'missions'
            for path in sorted(missions.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
                record = json.loads(path.read_text(encoding='utf-8'))
                if record.get('base_revision') == self.revision or record.get('final_revision') == self.revision:
                    lines.append('Mission: ' + str(record.get('mission')) + '\nState: ' + str(record.get('state')) +
                                 '\nCheckpoint: ' + str(record.get('checkpoint', record.get('checkpoint_status', 'See saved mission record'))))
                    break
            from .attention import current, review_text
            attention = current(self)
            heading = review_text(self.ws, self.project, attention['record']) if attention else ''
            self.show('Proposal', heading + '\n\n'.join(lines))
            self.tabs.select(3)
        self.guard(show)

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
            self.begin_query()
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
            self.launch_query(worker)
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
        focused = self.files.focus()
        values = self.files.item(focused, 'values') if focused else ()
        name = values[0] if values and values[0] in selected else (selected[-1] if selected else None)
        if not name:
            self.show('Source', '')
            self.show('Shadow', '')
            return
        root = self.ws.revision(self.project, self.revision)
        try:
            self.show('Source', read_source(root, name))
        except (OSError, ValueError, UnicodeError) as exc:
            self.show('Source', 'Cannot preview ' + name + ': ' + str(exc))
        try:
            shadows = json.loads((root / '.hornet-meta' / 'shadows.json').read_text(encoding='utf-8'))
            self.show('Shadow', json.dumps(shadows.get(name, {}), indent=2))
        except (OSError, ValueError, UnicodeError) as exc:
            self.show('Shadow', 'File context unavailable: ' + str(exc))

    def add_files_to_prompt(self):
        """Add references only: never submit a mission or change its edit scope."""
        if not self.project or not self.revision:
            return
        if getattr(self, '_prompt_paths_context', None) != (self.project, self.revision):
            return
        rows = self.files.selection()
        if not rows:
            self.status.set('Select files or folders to add to the prompt.')
            return
        try:
            from .prompt_paths import format_paths
            paths = [self._prompt_paths[row] for row in rows]
            text = format_paths(self.ws.revision(self.project, self.revision), paths)
        except (KeyError, OSError, ValueError) as exc:
            self.status.set('Cannot add paths to prompt: ' + str(exc))
            return
        # Append rather than replace selected prompt text. One undo restores it.
        existing = self.goal.get('1.0', 'end-1c')
        self.goal.edit_separator()
        self.goal.insert('end-1c', ('\n' if existing and not existing.endswith('\n') else '') + text)
        self.goal.edit_separator()
        self.goal.mark_set('insert', 'end-1c')
        self.goal.see('insert')
        self.goal.focus_set()
        self.status.set('Paths added to prompt. Describe how to use them; edit scope is unchanged.')

    def files_context_menu(self, event=None):
        keyboard = event is None or getattr(event, 'keysym', '') in ('F10', 'Menu')
        if keyboard:
            row = self.files.focus()
            if row and row not in self.files.selection():
                self.files.selection_set(row)
            box = self.files.bbox(row) if row else ()
            x = self.files.winfo_rootx() + 20
            y = self.files.winfo_rooty() + (box[1] + box[3] if box else 20)
        else:
            row = self.files.identify_row(event.y)
            if not row:
                return 'break'
            if row not in self.files.selection():
                self.files.selection_set(row)
            self.files.focus(row)
            x, y = event.x_root, event.y_root
        if not self.files.selection():
            return 'break'
        self.files.focus_set()
        menu = tk.Menu(self.files, tearoff=False)
        menu.add_command(label='Add to prompt', command=self.add_files_to_prompt)
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()
        return 'break'

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
        menu.add_separator()
        from .working_revision import is_working
        menu.add_command(label='Create working revision', command=lambda: self.copy_revision(project, revision))
        menu.add_command(label='Lock revision', command=lambda: self.lock_revision(project, revision),
                         state='normal' if is_working(self.ws, project, revision) else 'disabled')
        menu.add_command(label='Clean up checkpoints...', command=lambda: self.clean_checkpoints(project, revision),
                         state='disabled' if is_working(self.ws, project, revision) else 'normal')
        menu.add_command(label='Hide internal checkpoints' if getattr(self, 'show_checkpoints', False)
                         else 'Show internal checkpoints', command=self.toggle_checkpoints)
        menu.tk_popup(event.x_root, event.y_root)

    def toggle_checkpoints(self):
        if self.busy:
            return
        self.show_checkpoints = not getattr(self, 'show_checkpoints', False)
        self.refresh(self.project, self.revision)

    def discard_working_revision(self, project=None, revision=None):
        project, revision = project or self.project, revision or self.revision
        if self.busy or not project or not revision:
            return
        def perform():
            from .working_revision import abandon_working_revision
            if not messagebox.askyesno('Discard working revision?',
                    'Stop using this working revision? It will be locked and hidden from the working view.'
                    '\n\nRecovery history, saved missions, checkpoints, and source files remain. Nothing is deleted.'
                    '\n\nThe original mission will no longer resume automatically. Inspect any unresolved check outcomes separately.', parent=self.root):
                return
            from .working_revision import require_working
            source = require_working(self.ws, project, revision)['source']
            abandon_working_revision(self.ws, project, revision, discard=True)
            self.pending_mission = None
            self._working_selection_anchor = None
            self.refresh(project, read_state(self.ws, project).get('accepted_revision') or source)
            self.update_gate()
            self.status.set('Working revision discarded. Recovery history and source files remain; nothing was deleted.')
        self.guard(perform)

    def lock_revision(self, project=None, revision=None):
        project, revision = project or self.project, revision or self.revision
        if self.busy or not project or not revision:
            return
        def perform():
            from .working_revision import lock_working_revision
            lock_working_revision(self.ws, project, revision)
            self.refresh(project, revision)
            self.update_gate()
            self.status.set('Revision locked. Create a working revision to run more missions. Default unchanged.')
        self.guard(perform)

    def clean_checkpoints(self, project, revision):
        if self.busy:
            return
        def perform():
            from .candidate_view import cleanup_plan, cleanup_checkpoints
            plan = cleanup_plan(self.ws, project, revision)
            if not plan['revisions']:
                self.status.set('No unused checkpoints to clean up. Referenced revisions are preserved.')
                return
            names = '\n'.join(plan['revisions'])
            if not messagebox.askyesno('Clean up checkpoints',
                    'Move these ' + str(len(plan['revisions'])) + ' internal checkpoints to project Trash?\n\n'
                    + names + '\n\nThe original and final result stay. Files remain recoverable on disk; '
                    'this does not free disk space or permanently delete files.', parent=self.root):
                return
            count = cleanup_checkpoints(self.ws, project, revision, plan)
            self.refresh(project, revision)
            self.status.set(str(count) + ' checkpoints moved to recoverable Trash. Original and final result preserved.')
        self.guard(perform)

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
        from .revision_drag import confirm_export
        confirm_export(self, project, revision)

    def copy_revision(self, project=None, revision=None):
        project, revision = project or self.project, revision or self.revision
        if self.busy or not project or not revision:
            return
        def copy():
            require_ready(self.ws, project, revision)
            from .working_revision import create_working_revision
            working = create_working_revision(self.ws, project, revision)
            self.refresh(project, working)
            self.status.set('Working revision created. The previous working revision is locked; default unchanged.')
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
        from .understanding import UnderstandingEditor
        editor = UnderstandingEditor(dialog, json.loads((root / '.blueprint').read_text(encoding='utf-8')))
        editor.pack(fill='both', expand=True)
        dialog.title('Project understanding')
        def save():
            def perform():
                data = editor.value()['blueprint']
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
        if self.busy and self.operation_started is not None:
            elapsed = int(time.monotonic() - self.operation_started)
            self.status.set(f'{self.operation_phase} | {elapsed}s elapsed')
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            if kind == 'automation_progress':
                if self.cancel_token is not None and self.cancel_token.event.is_set():
                    self.root.after(100, self.poll)
                    return
                self.operation_phase = value
                self.activity(value)
                self.status.set(value + '…')
                self.root.after(100, self.poll)
                return
            if kind in ('automation_error', 'initialized_draft', 'diagnosis_complete', 'mission_review', 'plan_review'):
                cancelled = self.cancel_token is not None and self.cancel_token.event.is_set()
                self.cancel_token = None
                self.operation_started = None
                if cancelled:
                    self.busy = False
                    self.activity_indicator.stop()
                    if self.project:
                        path = self.ws.project(self.project) / 'initialization-draft.json'
                        if path.is_file():
                            self.pending_initialization = json.loads(path.read_text(encoding='utf-8'))
                    self.refresh(self.project, self.revision)
                    self.status.set('Stopped. Saved progress and your prompt are kept. Late model responses are ignored; the server may still be finishing.')
                    self.activity(self.status.get())
                    self.update_gate()
                    self.root.after(100, self.poll)
                    return
            if kind == 'plan_review':
                self.busy = False
                self.activity_indicator.stop()
                self.show_mission_plan(value)
                self.notify_response()
                self.update_gate()
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
                self.refresh(self.project, self.revision)
                self.status.set('Automation stopped. Partial work remains in the working revision. Resume the saved mission, keep partial work for a new mission, or discard it. The accepted revision is unchanged.')
                self.update_gate()
                messagebox.showerror('Hornet automation', value)
                self.root.after(100, self.poll)
                return
            if kind == 'initialized_draft':
                self.activity_indicator.stop()
                self.activity('Project understanding saved. Review it before continuing.')
                self.busy = False
                self.pending_initialization = value
                self.show_initialization(value)
                from .understanding import draft_complete
                if draft_complete(value):
                    self.notify_response()
                self.update_gate()
                self.root.after(100, self.poll)
                return
            if kind == 'diagnosis_complete':
                self.activity_indicator.stop()
                self.activity('Diagnosis ready for review.')
                self.busy = False
                self.show_diagnosis(value)
                self.notify_response()
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
                self.notify_response()
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
    parser.add_argument('--workspace', help='Override saved workspace folder (default without a saved choice: ~/.hornet)')
    parser.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = tk.Tk()
    from .workspace_settings import startup_root
    try:
        workspace = Workspace(startup_root(args.workspace))
    except (OSError, ValueError) as exc:
        root.withdraw()
        messagebox.showerror('Cannot open Hornet Coder folder', str(exc) +
                             '\nRestore access to the saved folder or launch with --workspace PATH.', parent=root)
        root.destroy()
        return
    App(root, workspace)
    if args.smoke_test:
        root.after(600, root.destroy)
    root.mainloop()
