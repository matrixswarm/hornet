import unittest
from calculator import double


class CalculatorTest(unittest.TestCase):
    def test_integer_return(self):
        for n in (-3, 0, 7):
            self.assertIs(type(double(n)), int)

    def test_zero(self):
        self.assertEqual(double(0), 0)
