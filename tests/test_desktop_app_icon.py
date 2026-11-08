"""Window branding must work without image files or optional dependencies."""
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from hornet.desktop.app_icon import _COLORS, _PIXELS, install_window_icon


class AppIconTests(unittest.TestCase):
    def test_pixel_art_is_complete(self):
        self.assertEqual(len(_PIXELS), 16)
        self.assertTrue(all(len(row) == 16 for row in _PIXELS))
        self.assertEqual(set(''.join(_PIXELS)), set(_COLORS) | {'.'})

    def test_sets_default_for_child_windows_and_retains_images(self):
        root = Mock()
        with patch('hornet.desktop.app_icon.tk.PhotoImage') as factory:
            self.assertTrue(install_window_icon(root))
            small = factory.return_value
            large = small.zoom.return_value
            factory.assert_called_once_with(master=root, width=16, height=16)
            root.iconphoto.assert_called_once_with(True, small, large)
            self.assertEqual(root._hornet_window_icons, (small, large))

    def test_unsupported_window_manager_does_not_break_startup(self):
        root = Mock()
        root.iconphoto.side_effect = tk.TclError('not supported')
        with patch('hornet.desktop.app_icon.tk.PhotoImage'):
            self.assertFalse(install_window_icon(root))

    def test_real_tk_icon(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        try:
            self.assertTrue(install_window_icon(root))
            small, large = root._hornet_window_icons
            self.assertEqual((small.width(), small.height()), (16, 16))
            self.assertEqual((large.width(), large.height()), (32, 32))
            self.assertTrue(small.transparency_get(0, 0))
            self.assertEqual(small.get(6, 4), (243, 184, 75))
            child = tk.Toplevel(root)
            child.withdraw()
            root.update_idletasks()
            child.destroy()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
