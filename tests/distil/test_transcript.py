import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import unittest
from hornet.distil.qt import QApplication
from hornet.distil.transcript import NumberedTranscript
from hornet.distil.model import build_messages


class TranscriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = NumberedTranscript()
        self.editor.resize(600, 350)
        self.editor.show()
        self.qt.processEvents()

    def tearDown(self):
        self.editor.close()
        self.qt.processEvents()

    def test_numbers_are_not_part_of_text_or_clipboard(self):
        text = 'YOU\n  Preserve whitespace  \n\nHORNET\nCafé and unicode 🐝\n'
        self.editor.setPlainText(text)
        self.qt.processEvents()
        self.assertEqual(self.editor.blockCount(), len(text.split('\n')))
        self.assertEqual(self.editor.toPlainText(), text)
        self.editor.selectAll()
        self.editor.copy()
        self.assertEqual(self.qt.clipboard().text(), text)
        self.assertEqual(build_messages([{'role':'user','content':text}])[0]['content'], text)
        self.assertTrue(self.editor.line_number_area.isVisible())

    def test_wrapped_rows_keep_logical_numbers_on_resize(self):
        text = 'first\n' + ('A long line that wraps naturally. ' * 35) + '\nlast'
        self.editor.setPlainText(text)
        self.qt.processEvents()
        self.assertEqual(self.editor.blockCount(), 3)
        for width in (600, 230, 900):
            self.editor.resize(width, 350)
            self.editor.verticalScrollBar().setValue(0)
            self.qt.processEvents()
            self.assertEqual(self.editor.blockCount(), 3)
            self.assertEqual(self.editor.toPlainText(), text)
            self.assertEqual(self.editor.visible_line_numbers()[0][0], 1)
            block = self.editor.document().findBlockByNumber(1)
            self.assertGreater(block.layout().lineCount(), 1)
            self.assertEqual(self.editor.visible_line_numbers()[1][0], 2)

    def test_numbers_follow_scroll_and_width_grows(self):
        self.editor.setPlainText('short')
        small_width = self.editor.line_number_width()
        self.editor.setPlainText('\n'.join('Line ' + str(i) for i in range(1, 1201)))
        self.qt.processEvents()
        self.assertGreater(self.editor.line_number_width(), small_width)
        bar = self.editor.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        self.qt.processEvents()
        numbers = self.editor.visible_line_numbers()
        self.assertGreater(numbers[0][0], 1)
        self.assertEqual(numbers[0][0], self.editor.firstVisibleBlock().blockNumber() + 1)
        for number, top in numbers:
            block = self.editor.document().findBlockByNumber(number - 1)
            expected = self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top()
            self.assertAlmostEqual(top, expected)
        self.assertEqual(self.editor.line_number_area.y(), self.editor.viewport().y())
        self.assertEqual(self.editor.line_number_area.height(), self.editor.viewport().height())
        self.editor.setPlainText('DISTILLED CONTEXT\nNew short summary')
        self.qt.processEvents()
        self.assertEqual(self.editor.blockCount(), 2)
        self.assertEqual([n for n, _ in self.editor.visible_line_numbers()], [1, 2])
        self.assertEqual(self.editor.line_number_width(), small_width)


if __name__ == '__main__':
    unittest.main()
