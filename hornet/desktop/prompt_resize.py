"""Resize the mission editor without replacing its text widget or draft."""
from tkinter import font as tkfont


class PromptResize:
    MIN_LINES = 3
    EDITOR_MIN_HEIGHT = 100

    def __init__(self, text, editor, handle):
        self.text = text
        self.editor = editor
        self.handle = handle
        self._drag = None
        handle.bind('<ButtonPress-1>', self.start)
        handle.bind('<B1-Motion>', self.drag)
        handle.bind('<ButtonRelease-1>', self.stop)
        handle.bind('<Up>', lambda event: self.step(1))
        handle.bind('<Down>', lambda event: self.step(-1))
        handle.bind('<Home>', lambda event: self.resize(self.MIN_LINES))
        handle.bind('<FocusIn>', lambda event: handle.configure(style='Section.TLabel'))
        handle.bind('<FocusOut>', lambda event: handle.configure(style='Muted.TLabel'))
        editor.bind('<Configure>', self.fit, add='+')
        editor.master.bind('<Configure>', self.fit, add='+')
        text.master.master.bind('<Configure>', self.fit, add='+')

    def line_height(self):
        return max(1, tkfont.Font(root=self.text, font=self.text.cget('font')).metrics('linespace'))

    def resize(self, lines):
        current = int(self.text.cget('height'))
        # Borrow only spare preview space. Keep the mission actions and a usable
        # preview visible even after the window shrinks or options are opened.
        available = self.editor.master.winfo_height()
        for child in self.editor.master.pack_slaves():
            if child is self.editor:
                continue
            pady = child.pack_info().get('pady', 0)
            values = pady if isinstance(pady, tuple) else self.text.tk.splitlist(str(pady))
            padding = sum(int(v) for v in values) if len(values) > 1 else 2 * int(values[0])
            available -= child.winfo_reqheight() + padding
        maximum = max(self.MIN_LINES, current +
                      (available - self.EDITOR_MIN_HEIGHT) // self.line_height())
        lines = max(self.MIN_LINES, min(int(lines), maximum))
        if lines != current:
            self.text.configure(height=lines)
        return 'break'

    def start(self, event):
        self._drag = (event.y_root, int(self.text.cget('height')))
        self.handle.focus_set()
        return 'break'

    def drag(self, event):
        if self._drag is not None:
            y, lines = self._drag
            self.resize(lines + round((y - event.y_root) / self.line_height()))
        return 'break'

    def stop(self, event):
        self._drag = None
        return 'break'

    def step(self, lines):
        return self.resize(int(self.text.cget('height')) + lines)

    def fit(self, event):
        self.resize(int(self.text.cget('height')))
