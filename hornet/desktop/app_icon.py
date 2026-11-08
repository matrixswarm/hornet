"""Original pixel-art hornet, bundled as code so every install has its icon.

Tk iconphoto works on Windows and X11 without an .ico file or imaging library.
Unsupported window managers retain their default icon without blocking startup.
"""
import tkinter as tk

# Transparent 16px silhouette: antennae, pale wings, amber stripes and stinger.
_PIXELS = (
    "................",
    ".....D....D.....",
    "......D..D......",
    "......DDDD......",
    ".....DAAAAD.....",
    "..DDDDAAAADDDD..",
    ".DWWWDDDDDDWWWD.",
    ".DWWWDAAAADWWWD.",
    "..DWWDAAAADWWD..",
    "...DDDDDDDDDD...",
    "..DWWDAAAADWWD..",
    "...DDDAAAADDD...",
    ".....DDDDDD.....",
    "......DAAD......",
    ".......DD.......",
    "................",
)
_COLORS = {'D': '#17212d', 'A': '#f3b84b', 'W': '#c5e9f5'}


def install_window_icon(root):
    """Set this and future child windows' icons, retaining Tcl image owners."""
    try:
        small = tk.PhotoImage(master=root, width=16, height=16)
        for y, row in enumerate(_PIXELS):
            for x, color in enumerate(row):
                if color != '.':
                    small.put(_COLORS[color], (x, y))
        large = small.zoom(2, 2)
        root.iconphoto(True, small, large)
        root._hornet_window_icons = (small, large)
        return True
    except tk.TclError:
        # Cosmetic branding must not prevent startup on unsupported desktops.
        return False
