"""Choose which Hornet desktop application to open."""
import importlib.util
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox


def launch(choice):
    if choice not in ('coder', 'distil'):
        raise ValueError('Unknown Hornet application.')
    if choice == 'distil' and (importlib.util.find_spec('requests') is None or
                              (importlib.util.find_spec('PyQt6') is None and
                               importlib.util.find_spec('PyQt5') is None)):
        raise RuntimeError('Distil needs its optional dependencies. Install Hornet with the distil extra.')
    return subprocess.Popen([sys.executable, '-m', 'hornet.' + choice], close_fds=True)


def main():
    root = tk.Tk()
    root.title('Hornet')
    root.configure(bg='#10151d')
    root.resizable(False, False)
    from hornet.desktop.app_icon import install_window_icon
    install_window_icon(root)
    frame = tk.Frame(root, bg='#10151d', padx=30, pady=24)
    frame.pack()
    tk.Label(frame, text='HORNET', bg='#10151d', fg='#f3b84b',
             font=('TkDefaultFont', 20, 'bold')).pack(pady=(0, 8))
    tk.Label(frame, text='Choose an application', bg='#10151d', fg='#e6edf5').pack(pady=(0, 16))

    def open_app(choice):
        try:
            launch(choice)
        except (OSError, RuntimeError) as exc:
            messagebox.showerror('Unable to start Hornet', str(exc), parent=root)
            return
        root.destroy()

    for choice, label in (('coder', 'Open Coder'), ('distil', 'Open Distil')):
        tk.Button(frame, text=label, width=25, command=lambda c=choice: open_app(c),
                  bg='#263549', fg='#e6edf5', activebackground='#334863',
                  activeforeground='#ffffff', relief='flat', pady=8).pack(pady=4)
    root.mainloop()


if __name__ == '__main__':
    main()
