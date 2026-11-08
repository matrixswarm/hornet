"""Hierarchical file selection; only inventoried files can be selected."""
import tkinter as tk
from tkinter import ttk
from pathlib import PurePosixPath
from .file_selection import default_excluded


class FilePicker(ttk.Frame):
    def __init__(self, parent, names, selected=None):
        super().__init__(parent)
        self.names = sorted(set(names))
        self.chosen = (set(selected) & set(self.names) if selected is not None else
                       {name for name in self.names if not default_excluded(name)})
        ttk.Label(self, text='Click Included or press Space to toggle. A folder applies to all its files.\n'
                  'Expand folders to choose individual files. Partial means only some are included.').pack(anchor='w')
        body = ttk.Frame(self)
        body.pack(fill='both', expand=True, pady=8)
        self.tree = ttk.Treeview(body, columns=('included',), selectmode='extended')
        self.tree.heading('#0', text='Folder / file')
        self.tree.heading('included', text='Included')
        self.tree.column('#0', width=520, minwidth=180)
        self.tree.column('included', width=100, minwidth=90, stretch=False)
        scroll = ttk.Scrollbar(body, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.members = {}
        self.rows = {}
        for name in self.names:
            parts = PurePosixPath(name).parts
            parent_row = ''
            for index, part in enumerate(parts):
                path = '/'.join(parts[:index + 1])
                if path not in self.rows:
                    self.rows[path] = self.tree.insert(parent_row, 'end', text=part,
                                                       open=index == 0)
                    self.members[self.rows[path]] = set()
                parent_row = self.rows[path]
                self.members[parent_row].add(name)
        self.count = tk.StringVar()
        actions = ttk.Frame(self)
        actions.pack(fill='x')
        ttk.Label(actions, textvariable=self.count).pack(side='left')
        ttk.Button(actions, text='Exclude selected', command=lambda: self.set_rows(False)).pack(side='right', padx=4)
        ttk.Button(actions, text='Include selected', command=lambda: self.set_rows(True)).pack(side='right', padx=4)
        self.tree.bind('<Button-1>', self.click)
        self.tree.bind('<space>', self.space)
        self.render()

    def selected_files(self):
        return sorted(self.chosen)

    def render(self):
        for row, files in self.members.items():
            count = len(files & self.chosen)
            state = 'Yes' if count == len(files) else 'No' if count == 0 else 'Partial'
            self.tree.item(row, values=(state,))
        self.count.set(f'{len(self.chosen)} of {len(self.names)} files included')

    def set_rows(self, include, rows=None):
        files = set().union(*(self.members[row] for row in
                            (self.tree.selection() if rows is None else rows)))
        if include:
            self.chosen.update(files)
        else:
            self.chosen.difference_update(files)
        self.render()

    def toggle(self, rows):
        files = set().union(*(self.members[row] for row in rows))
        self.set_rows(not files.issubset(self.chosen), rows)

    def click(self, event):
        row = self.tree.identify_row(event.y)
        if row and self.tree.identify_column(event.x) == '#1':
            self.tree.selection_set(row)
            self.tree.focus(row)
            self.tree.focus_set()
            self.toggle((row,))
            return 'break'

    def space(self, _event=None):
        self.toggle(self.tree.selection())
        return 'break'
