import tkinter as tk
import unittest
from unittest.mock import patch
from hornet.desktop.status_light import StatusLight


class StatusLightTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.light = StatusLight(self.root)

    def tearDown(self):
        self.root.destroy()

    def tick(self):
        self.root.after_cancel(self.light._timer)
        self.light._timer = None
        self.light._tick()

    def test_busy_state_and_idle_reset(self):
        self.light.start(12)
        self.assertTrue(self.light.busy)
        self.assertEqual(self.light.itemcget(self.light._line, 'fill'), '#aa8b53')
        self.light.stop()
        self.assertFalse(self.light.busy)
        self.assertTrue(all(self.light.itemcget(i, 'state') == 'hidden' for i in self.light._pulse))

    def test_active_beam_is_wider_brighter_and_resets_on_stop(self):
        self.light.draw(0.5)
        idle = self.light.coords(self.light._pulse[-1])
        self.light.start()
        self.light.draw(0.5)
        active = self.light.coords(self.light._pulse[-1])
        self.assertGreater(active[2] - active[0], idle[2] - idle[0])
        self.assertEqual(self.light.itemcget(self.light._pulse[-1], 'fill'), '#fff0ca')
        self.assertEqual(self.light.itemcget(self.light._halo, 'state'), 'normal')
        self.light.stop()
        self.assertEqual(self.light.itemcget(self.light._halo, 'state'), 'hidden')

    def test_repeated_start_does_not_reset_animation_or_duplicate_timer(self):
        self.light.start()
        timer = self.light._timer
        self.light._started = 42
        self.light.start()
        self.assertEqual(self.light._timer, timer)
        self.assertEqual(self.light._started, 42)
        self.light.stop()
        timer = self.light._timer
        self.light.stop()
        self.assertEqual(self.light._timer, timer)

    def test_active_cycle_has_short_pause(self):
        self.light.start()
        with patch('hornet.desktop.status_light.motion_enabled', return_value=True), \
             patch.object(self.light, 'winfo_ismapped', return_value=True), \
             patch('hornet.desktop.status_light.time.monotonic', side_effect=[10, 11.6]):
            self.tick()
            with patch.object(self.light, '_schedule') as schedule:
                self.tick()
                schedule.assert_called_once_with(250)

    def test_reduced_motion_disables_pulse(self):
        with patch('hornet.desktop.status_light.motion_enabled', return_value=False):
            self.light.start()
            self.tick()
            self.assertIsNone(self.light._started)
            self.assertTrue(all(self.light.itemcget(i, 'state') == 'hidden' for i in self.light._pulse))

    def test_pulse_and_pause_then_destroy_cancel_timer(self):
        with patch('hornet.desktop.status_light.motion_enabled', return_value=True), \
             patch.object(self.light, 'winfo_ismapped', return_value=True), \
             patch('hornet.desktop.status_light.time.monotonic', side_effect=[10, 12]):
            self.tick()
            self.assertTrue(all(self.light.itemcget(i, 'state') == 'normal' for i in self.light._pulse))
            self.tick()
            self.assertIsNone(self.light._started)
        self.light.destroy()
        self.assertIsNone(self.light._timer)


if __name__ == '__main__':
    unittest.main()
