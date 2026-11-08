"""A quiet status light, with motion disabled by the Windows animation setting."""
import sys
import time
import tkinter as tk


def motion_enabled():
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        enabled = ctypes.c_int()
        return bool(ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
                    and enabled.value)
    except (AttributeError, OSError):
        return False


class StatusLight(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=6, background='#10151d', highlightthickness=0, **kwargs)
        self.busy = False
        self._timer = None
        self._scheduler = self.winfo_toplevel()
        self._started = None
        self._halo = self.create_rectangle(0, 0, 1, 6, fill='#28251e', outline='')
        self._line = self.create_rectangle(0, 1, 1, 3, fill='#655a43', outline='')
        self._pulse = [self.create_rectangle(0, 0, 0, 0, fill=color, outline='', state='hidden')
                       for color in ('#776648', '#9a8053', '#c2a269', '#e6cd96', '#c2a269', '#9a8053', '#776648')]
        self.bind('<Configure>', lambda _: self.draw())
        self.bind('<Destroy>', self._destroyed)
        self.draw()
        self._schedule(5000)

    def _schedule(self, delay):
        if self._timer is not None:
            self._scheduler.after_cancel(self._timer)
        self._timer = self._scheduler.after(delay, self._tick)

    def draw(self, progress=None):
        width = self.winfo_width()
        self.coords(self._line, 0, 2, width, 4)
        self.coords(self._halo, 0, 0, width, 6)
        self.itemconfigure(self._halo, state='normal' if self.busy else 'hidden')
        colors = (('#75603c', '#907444', '#b18a49', '#d4a451', '#eabe72', '#f4d79c', '#fff0ca')
                  if self.busy else
                  ('#776648', '#9a8053', '#c2a269', '#e6cd96', '#c2a269', '#9a8053', '#776648'))
        segment = 28 if self.busy else 14
        self.itemconfigure(self._line, fill='#aa8b53' if self.busy else '#655a43')
        for index, item in enumerate(self._pulse):
            if progress is None:
                self.itemconfigure(item, state='hidden')
            else:
                x = progress * (width + segment * 7) - segment * 7 + index * segment
                self.coords(item, x, 1 if self.busy else 2, x + segment, 5 if self.busy else 4)
                self.itemconfigure(item, state='normal', fill=colors[index])

    def _tick(self):
        self._timer = None
        if not motion_enabled() or not self.winfo_ismapped():
            self._started = None
            self.draw()
            self._schedule(1500)
            return
        now = time.monotonic()
        if self._started is None:
            self._started = now
        progress = (now - self._started) / (1.5 if self.busy else 1.8)
        if progress >= 1:
            self._started = None
            self.draw()
            self._schedule(250 if self.busy else 6500)
        else:
            self.draw(progress)
            self._schedule(40)

    def start(self, _interval=None):
        if self.busy:
            return
        self.busy = True
        self._started = None
        self.draw()
        self._schedule(100)

    def stop(self):
        if not self.busy:
            return
        self.busy = False
        self._started = None
        self.draw()
        self._schedule(6500)

    def _destroyed(self, event):
        if event.widget is self and self._timer is not None:
            self._scheduler.after_cancel(self._timer)
            self._timer = None
