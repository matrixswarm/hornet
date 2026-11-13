"""A plain-text transcript with display-only, logical line numbers."""
from .qt import Qt, QSize, QEvent, QColor, QPainter, QPlainTextEdit, QWidget


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setAccessibleName('Chat line numbers')
        self.setToolTip('Text line numbers. Wrapped rows keep the same line number.')

    def sizeHint(self):
        return QSize(self.editor.line_number_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_numbers(event)


class NumberedTranscript(QPlainTextEdit):
    """Number newline-delimited blocks, never insert numbers into the document."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAccessibleName('Revision chat')
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self.update_line_number_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.update_line_number_width()

    def line_number_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 16 + self.fontMetrics().horizontalAdvance('9') * digits

    def update_line_number_width(self, *_):
        width = self.line_number_width()
        if self.viewportMargins().left() != width:
            self.setViewportMargins(width, 0, 0, 0)
        self._position_line_number_area()
        self.line_number_area.update()

    def _position_line_number_area(self):
        self.line_number_area.setGeometry(self.contentsRect().left(), self.viewport().y(),
            self.line_number_width(), self.viewport().height())

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_line_number_area()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, 'line_number_area') and event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.update_line_number_width()

    def visible_line_numbers(self):
        """(One-based logical line, viewport top) for visible text blocks."""
        result = []
        block = self.firstVisibleBlock()
        while block.isValid():
            top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
            if top > self.viewport().height():
                break
            bottom = top + self.blockBoundingRect(block).height()
            if block.isVisible() and bottom >= 0:
                result.append((block.blockNumber() + 1, top))
            block = block.next()
        return result

    def paint_line_numbers(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor('#111a25'))
        painter.setPen(QColor('#8193a9'))
        painter.setFont(self.font())
        for number, top in self.visible_line_numbers():
            painter.drawText(0, round(top), self.line_number_width() - 8,
                self.fontMetrics().height(), Qt.AlignmentFlag.AlignRight, str(number))
