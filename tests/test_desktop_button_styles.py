import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from hornet.desktop.app import App
from hornet.desktop.workspace import Workspace


def luminance(color):
    channels = [int(color[i:i+2],16)/255 for i in (1,3,5)]
    linear = [x/12.92 if x<=0.04045 else ((x+0.055)/1.055)**2.4 for x in channels]
    return sum(x*y for x,y in zip(linear,(0.2126,0.7152,0.0722)))

class ButtonStyleTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root=tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.temp=tempfile.TemporaryDirectory()
        self.app=App(self.root,Workspace(self.temp.name))
        self.style=ttk.Style(self.root)

    def tearDown(self):
        if hasattr(self,'root'):
            for callback in self.root.tk.splitlist(self.root.tk.call('after','info')):
                self.root.after_cancel(callback)
            self.root.destroy()
        if hasattr(self,'temp'):
            self.temp.cleanup()

    def test_flat_high_contrast_normal_hover_pressed_and_focus(self):
        for name in ('TButton','TMenubutton','Accent.TButton'):
            for state in ((),('active',),('pressed',),('active','pressed'),('focus',),('focus','active')):
                with self.subTest(style=name,state=state):
                    bg=self.style.lookup(name,'background',state=state)
                    fg=self.style.lookup(name,'foreground',state=state)
                    hi,lo=sorted((luminance(bg),luminance(fg)),reverse=True)
                    self.assertGreaterEqual((hi+0.05)/(lo+0.05),4.5)
                    self.assertEqual(str(self.style.lookup(name,'relief',state=state)),'flat')
                    self.assertEqual(str(self.style.lookup(name,'shiftrelief',state=state)),'0')
                    self.assertEqual(self.style.lookup(name,'lightcolor',state=state),bg)
                    self.assertEqual(self.style.lookup(name,'darkcolor',state=state),bg)

    def test_disabled_precedes_hover_and_press(self):
        for name in ('TButton','TMenubutton','Accent.TButton'):
            for option in ('background','foreground','bordercolor','lightcolor','darkcolor'):
                normal=self.style.lookup(name,option,state=('disabled',))
                self.assertEqual(normal,self.style.lookup(name,option,state=('disabled','active','pressed')))

    def test_menu_uses_chevron_not_raised_bar_indicator(self):
        self.assertNotIn('Menubutton.indicator',repr(self.style.layout('TMenubutton')))
        self.assertTrue(self.app.new_menu_button.cget('text').endswith('\u25be'))
        self.assertTrue(self.app.project_menu_button.cget('text').endswith('\u25be'))
        self.assertTrue(self.app.new_menu_button.cget('menu'))
        self.assertTrue(self.app.project_menu_button.cget('menu'))

    def test_selected_tab_is_distinct_and_persists_through_hover_and_focus(self):
        style='TNotebook.Tab'
        selected=self.style.lookup(style,'background',state=('selected',))
        self.assertNotEqual(selected,self.style.lookup(style,'background',state=()))
        self.assertNotEqual(selected,self.style.lookup(style,'background',state=('active',)))
        for state in (('selected',),('selected','active'),('selected','focus'),('selected','active','focus')):
            self.assertEqual(self.style.lookup(style,'background',state=state),selected)
            fg=self.style.lookup(style,'foreground',state=state)
            hi,lo=sorted((luminance(selected),luminance(fg)),reverse=True)
            self.assertGreaterEqual((hi+0.05)/(lo+0.05),4.5)
            self.assertEqual(self.style.lookup(style,'bordercolor',state=state),'#f3b84b')
        for index in range(len(self.app.tabs.tabs())):
            self.app.tabs.select(index)
            self.root.update_idletasks()
            self.assertEqual(self.app.tabs.index('current'),index)
        for option in ('background','foreground','bordercolor','lightcolor','darkcolor'):
            self.assertEqual(self.style.lookup(style,option,state=('disabled',)),
                             self.style.lookup(style,option,state=('disabled','selected','active')))

    def test_tab_bounds_do_not_change_when_selection_moves(self):
        # Offscreen real Tk layout, independent of the user's running instance.
        self.root.geometry('1380x900+30000+30000')
        self.root.deiconify()
        self.root.update()
        tabs=self.app.tabs
        if not tabs.winfo_ismapped() or tabs.winfo_width() <= 1:
            self.skipTest('Tk did not map the notebook in this desktop session')
        def bounds():
            found={}
            # Identify pixels belonging to each tab; this measures the rendered
            # tab boxes rather than just requested style values.
            for y in range(80):
                for x in range(tabs.winfo_width()):
                    try:
                        index=tabs.index('@%d,%d' % (x,y))
                    except tk.TclError:
                        continue
                    if index not in found:
                        found[index]=[x,y,x,y]
                    else:
                        box=found[index]
                        box[0]=min(box[0],x);box[1]=min(box[1],y)
                        box[2]=max(box[2],x);box[3]=max(box[3],y)
            return found
        baseline=bounds()
        self.assertEqual(len(baseline),len(tabs.tabs()))
        for index in range(len(tabs.tabs())):
            tabs.select(index)
            self.root.update_idletasks()
            self.assertEqual(bounds(),baseline, 'Tab geometry moved selecting %s' % index)
        heights={box[3]-box[1] for box in baseline.values()}
        self.assertEqual(len(heights),1)
        for state in ((),('selected',),('selected','active'),('selected','focus'),('disabled',)):
            value=self.style.lookup('TNotebook.Tab','padding',state=state)
            self.assertEqual(tuple(int(str(item)) for item in self.root.tk.splitlist(value)),(14,9))
