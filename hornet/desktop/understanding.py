"""Plain-language review over the existing validated project-map format."""
import copy
import json
import tkinter as tk
from tkinter import ttk


def describe(value, indent=''):
    if isinstance(value, dict):
        return '\n'.join(indent + str(key).replace('_', ' ').capitalize() + ': ' +
                         (describe(item, indent + '  ') if isinstance(item, (dict, list)) else str(item))
                         for key, item in value.items())
    if isinstance(value, list):
        return '\n'.join(indent + '- ' + describe(item, indent + '  ') for item in value)
    return str(value)


def draft_complete(draft):
    from .initialization import validate_draft
    try:
        validate_draft(draft)
    except (ValueError, TypeError, KeyError):
        return False
    return True


def draft_status(draft):
    count = len(draft.get('summaries', {}))
    total = len(draft.get('included', []))
    if draft_complete(draft):
        return f'Understanding ready for review: {count} of {total} files. Not accepted yet.'
    return f'Understanding incomplete: {count} of {total} files completed. Resume understanding to continue.'


def review_text(draft):
    result = ['PROJECT UNDERSTANDING', draft_status(draft), describe({k: v for k, v in draft['blueprint'].items() if k != 'status'}),
              f"Reviewed {len(draft.get('summaries', {}))} of {len(draft.get('included', []))} selected files."]
    if not draft_complete(draft):
        result.append('Not ready yet. Use Understand project to retry incomplete analysis.')
        result.append('Unreviewed files: ' + ', '.join(draft.get('unreviewed', [])))
        result.append(describe(draft.get('issues', {})))
    else:
        result.append('Does this describe your project? Choose Looks right, continue or Correct understanding.')
    if draft.get('review_notes'):
        result.append('Review notes\n' + describe(draft['review_notes']))
    result.append('File descriptions\n' + '\n'.join(name + ': ' + text for name, text in
                                                    list(draft.get('summaries', {}).items())[:12]))
    if len(draft.get('summaries', {})) > 12:
        result.append('More file descriptions are available in Correct understanding and Details.')
    return '\n\n'.join(part for part in result if part)


def show_details(parent, value):
    dialog = tk.Toplevel(parent)
    dialog.title('Understanding details')
    dialog.geometry('850x600')
    text = tk.Text(dialog, wrap='word')
    scroll = ttk.Scrollbar(dialog, command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    text.pack(fill='both', expand=True)
    text.insert('1.0', json.dumps(value, indent=2))
    text.configure(state='disabled')
    return dialog


class UnderstandingEditor(ttk.Frame):
    def __init__(self, parent, blueprint, summaries=None):
        super().__init__(parent, padding=14)
        self.original = copy.deepcopy(blueprint)
        self.summaries = copy.deepcopy(summaries)
        self.fields = {}
        ttk.Label(self, text='Correct the description below. Saving keeps it pending until you approve it.',
                  wraplength=700).pack(anchor='w', pady=(0, 10))
        for key, label in [('purpose', 'What does this project do?'), ('architecture', 'How is it organized?')]:
            if key in blueprint and not isinstance(blueprint[key], str):
                ttk.Label(self, text=label + '\n' + describe(blueprint[key]), wraplength=700).pack(anchor='w')
                continue
            ttk.Label(self, text=label).pack(anchor='w')
            field = tk.Text(self, height=4, wrap='word', undo=True)
            field.insert('1.0', blueprint.get(key, ''))
            field.pack(fill='x', pady=(4, 10))
            self.fields[key] = field
        self.current = None
        if summaries is not None:
            ttk.Label(self, text='File description (optional correction)').pack(anchor='w')
            self.file = ttk.Combobox(self, values=sorted(summaries), state='readonly')
            self.file.pack(fill='x', pady=4)
            self.summary = tk.Text(self, height=4, wrap='word', undo=True)
            self.summary.pack(fill='both', expand=True)
            self.file.bind('<<ComboboxSelected>>', self.select_file)
            if summaries:
                self.file.current(0)
                self.select_file()
        ttk.Button(self, text='Details', command=self.edit_details).pack(anchor='w', pady=8)

    def edit_details(self):
        from tkinter import messagebox
        dialog = show_details(self, self.value())
        text = next(child for child in dialog.winfo_children() if isinstance(child, tk.Text))
        text.configure(state='normal')
        def apply():
            try:
                value = json.loads(text.get('1.0', 'end'))
                blueprint = value.get('blueprint') if isinstance(value, dict) else None
                if not isinstance(blueprint, dict) or not blueprint or len(json.dumps(blueprint).encode('utf-8')) > 128_000:
                    raise ValueError('Use a nonempty blueprint object under 128 KB.')
                summaries = value.get('summaries')
                if self.summaries is not None and (not isinstance(summaries, dict) or
                        any(not isinstance(v, str) or not v.strip() or len(v) > 4000 for v in summaries.values())):
                    raise ValueError('File descriptions must be nonempty text under 4,000 characters.')
                self.original = copy.deepcopy(blueprint)
                for key, field in self.fields.items():
                    field.configure(state='normal')
                    field.delete('1.0', 'end')
                    field.insert('1.0', describe(blueprint.get(key, '')))
                    if key in blueprint and not isinstance(blueprint[key], str):
                        field.configure(state='disabled')
                if self.summaries is not None:
                    self.summaries = copy.deepcopy(summaries)
                    self.current = None
                    self.file.configure(values=sorted(summaries))
                    self.file.set(sorted(summaries)[0] if summaries else '')
                    self.select_file()
                dialog.destroy()
            except (ValueError, TypeError) as exc:
                messagebox.showerror('Understanding details', str(exc), parent=dialog)
        ttk.Button(dialog, text='Apply to correction form', command=apply).pack(side='bottom', before=text)

    def select_file(self, _event=None):
        if self.current is not None:
            self.summaries[self.current] = self.summary.get('1.0', 'end-1c').strip()
        self.current = self.file.get() or None
        self.summary.delete('1.0', 'end')
        self.summary.insert('1.0', self.summaries.get(self.current, ''))

    def value(self):
        blueprint = copy.deepcopy(self.original)
        for key, field in self.fields.items():
            if str(field.cget('state')) == 'disabled':
                continue
            value = field.get('1.0', 'end-1c').strip()
            if value or key in blueprint:
                blueprint[key] = value
        result = {'blueprint': blueprint}
        if self.summaries is not None:
            if self.current is not None:
                self.summaries[self.current] = self.summary.get('1.0', 'end-1c').strip()
            result['summaries'] = copy.deepcopy(self.summaries)
        return result
