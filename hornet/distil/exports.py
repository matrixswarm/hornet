"""Explicit exports of a revision's active conversation, never its archive/draft."""
import html
import math
import os
from pathlib import Path
import re
import tempfile
from contextlib import suppress


EXTENSIONS = {'PDF': '.pdf', 'Text': '.txt', 'Markdown': '.md'}


def transcript_text(messages):
    parts = []
    for entry in messages:
        if entry.get('kind') == 'distilled':
            parts.append('──────── DISTILLED CONTEXT ────────\n' + entry['content'] + '\n──────── END DISTILL ────────')
        else:
            parts.append(('YOU' if entry['role'] == 'user' else 'HORNET') + '\n' + entry['content'])
    return '\n\n'.join(parts)


def normalize_export_path(path, format):
    if format not in EXTENSIONS:
        raise ValueError('Choose PDF, Text, or Markdown.')
    path = Path(path).expanduser()
    if not path.name or path.name in ('.', '..'):
        raise ValueError('Choose a filename.')
    suffix = EXTENSIONS[format]
    if path.suffix.lower() != suffix:
        path = path.with_suffix(suffix)
    return path


def suggested_filename(project_name, revision_name):
    name = str(project_name) + ' - ' + str(revision_name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip(' .')[:140].rstrip(' .')
    if not name:
        return 'Hornet revision'
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM%d' % i for i in range(1, 10)), *('LPT%d' % i for i in range(1, 10))}:
        name = '_' + name
    return name


def _single_line(value):
    return ' '.join(str(value).splitlines())


def _markdown_heading(value):
    return re.sub(r'([\\`*_{}\[\]<>()#+.!|>-])', r'\\\1', _single_line(value))


def _text(project, state):
    return ('Project: ' + _single_line(project) + '\nRevision: ' + _single_line(state['name']) +
            '\n\n' + (transcript_text(state['messages']) or '(No messages.)') + '\n')


def _markdown(project, state):
    parts = ['# ' + _markdown_heading(project), '## Revision: ' + _markdown_heading(state['name'])]
    for entry in state['messages']:
        label = 'Distilled context' if entry.get('kind') == 'distilled' else ('You' if entry['role'] == 'user' else 'Hornet')
        parts.append('### ' + label + '\n\n' + entry['content'])
    if not state['messages']:
        parts.append('(No messages.)')
    return '\n\n'.join(parts) + '\n'


def _write_pdf(path, project, state):
    # Match the binding selected by the app; do not import both into one process.
    from .qt import QApplication
    if QApplication.instance() is None:
        raise RuntimeError('Open Hornet Distil before exporting a PDF.')
    if QApplication.__module__.startswith('PyQt6'):
        from PyQt6.QtCore import QMarginsF, Qt, QUrl
        from PyQt6.QtGui import QFont, QFontMetricsF, QImage, QPainter, QPageLayout, QPageSize, QPdfWriter, QTextDocument, QTextOption
    else:
        from PyQt5.QtCore import QMarginsF, Qt, QUrl
        from PyQt5.QtGui import QFont, QFontMetricsF, QImage, QPainter, QPageLayout, QPageSize, QPdfWriter, QTextDocument, QTextOption
    writer = QPdfWriter(str(path))
    writer.setResolution(96)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(18, 18, 18, 18), QPageLayout.Unit.Millimeter)
    writer.setTitle(_single_line(project) + ' / ' + _single_line(state['name']))
    writer.setCreator('Hornet Distil')
    document = QTextDocument()
    document.setDefaultFont(QFont('DejaVu Sans', 10))
    option = document.defaultTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option)
    document.setDefaultStyleSheet('h1 { font-size:18pt; color:#172c40; margin-bottom:5pt; } '
        'h2 { font-size:12pt; color:#40556a; margin-bottom:18pt; } '
        'h3 { font-size:10pt; color:#172c40; margin-top:14pt; margin-bottom:5pt; } '
        'div { white-space:pre-wrap; line-height:130%; }')
    # Qt 5's PDF font embedding drops color-font emoji. Rasterize only those
    # runs at high resolution; surrounding text remains searchable/vector text.
    emoji_runs = re.compile(r'[🇦-🇿]{2}|[🀀-🫿☀-➿][︎️🏻-🏿]*(?:‍[🀀-🫿☀-➿][︎️🏻-🏿]*)*')
    image_number = 0
    def pdf_html(text):
        nonlocal image_number
        parts, start = [], 0
        for match in emoji_runs.finditer(text):
            parts.append(html.escape(text[start:match.start()]))
            font = QFont('Noto Color Emoji')
            font.setPixelSize(64)
            metrics = QFontMetricsF(font)
            width = max(1, math.ceil(metrics.horizontalAdvance(match.group()))) + 4
            height = max(1, math.ceil(metrics.height())) + 4
            bitmap = QImage(width, height, QImage.Format.Format_ARGB32)
            bitmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(bitmap)
            painter.setFont(font)
            painter.drawText(2, math.ceil(metrics.ascent()) + 2, match.group())
            painter.end()
            url = QUrl('hornet-emoji:' + str(image_number))
            image_number += 1
            document.addResource(QTextDocument.ResourceType.ImageResource, url, bitmap)
            parts.append('<img src="' + url.toString() + '" width="' + str(round(width * 16 / height, 2)) + '" height="16" />')
            start = match.end()
        parts.append(html.escape(text[start:]))
        return ''.join(parts)
    sections = ['<h1>' + pdf_html(_single_line(project)) + '</h1>',
                '<h2>Revision: ' + pdf_html(_single_line(state['name'])) + '</h2>']
    for entry in state['messages']:
        label = 'Distilled context' if entry.get('kind') == 'distilled' else ('You' if entry['role'] == 'user' else 'Hornet')
        sections.append('<h3>' + label + '</h3><div>' + pdf_html(entry['content']) + '</div>')
    if not state['messages']:
        sections.append('<p>(No messages.)</p>')
    document.setHtml(''.join(sections))
    printer = getattr(document, 'print_', None) or document.print
    printer(writer)
    # Destroy the writer before validating the complete file/EOF marker.
    del writer
    data = Path(path).read_bytes()
    if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-1024:]:
        raise OSError('PDF generation did not finish. Existing file was not changed.')


def export_revision(path, project_name, revision_state, format, overwrite=False):
    """Atomically export a snapshot. The caller confirms the final normalized path.

    This function never writes revision state. A no-overwrite publication uses a
    hard link so a file appearing after the save dialog is not silently replaced.
    """
    target = normalize_export_path(path, format)
    if target.is_symlink():
        raise ValueError('Choose a regular file, not a symbolic link.')
    if target.exists() and not overwrite:
        raise FileExistsError('File already exists; confirm replacement first.')
    if target.exists() and not target.is_file():
        raise ValueError('Choose a regular file.')
    fd, temporary = tempfile.mkstemp(prefix='.hornet-export-', suffix=EXTENSIONS[format], dir=str(target.parent))
    staged = Path(temporary)
    os.close(fd)
    try:
        if format == 'PDF':
            _write_pdf(staged, project_name, revision_state)
        else:
            data = _text(project_name, revision_state) if format == 'Text' else _markdown(project_name, revision_state)
            staged.write_text(data, encoding='utf-8')
        # Windows requires a writable handle for flushing file buffers.
        with staged.open('r+b') as handle:
            os.fsync(handle.fileno())
        # Check again after rendering; a symlink cannot be selected by accident.
        if target.is_symlink():
            raise ValueError('The destination became a symbolic link; choose another file.')
        if overwrite:
            os.replace(staged, target)
        else:
            os.link(staged, target)
        return target
    finally:
        with suppress(OSError):
            staged.unlink(missing_ok=True)
