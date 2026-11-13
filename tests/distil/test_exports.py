import copy
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from hornet.distil import exports
from hornet.distil.qt import QApplication


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = {'name':'Revision 1', 'draft':'PRIVATE UNSENT DRAFT', 'archive':['ARCHIVED SECRET'], 'messages':[
            {'role':'assistant','kind':'distilled','content':'  Café résumé\nDecisions: α + β = γ\n✨🐝'},
            {'role':'user','content':'First line\n\nThird line: <script>not HTML</script>'},
            {'role':'assistant','content':'The next step is undecided.'}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_transcript_matches_existing_display(self):
        result=exports.transcript_text(self.state['messages'])
        self.assertTrue(result.startswith('──────── DISTILLED CONTEXT ────────\n  Café'))
        self.assertIn('\n──────── END DISTILL ────────\n\nYOU\nFirst line', result)
        self.assertTrue(result.endswith('HORNET\nThe next step is undecided.'))

    def test_extensions_and_safe_names(self):
        for format,suffix in exports.EXTENSIONS.items():
            self.assertEqual(exports.normalize_export_path(self.root/'wrong.old',format).suffix,suffix)
            self.assertEqual(exports.normalize_export_path(self.root/'untitled',format).suffix,suffix)
        self.assertEqual(exports.normalize_export_path('upper.PDF','PDF').name,'upper.PDF')
        name=exports.suggested_filename('../Unsafe:/project','revision?*\nname')
        self.assertNotIn('/',name);self.assertNotIn('?',name);self.assertNotIn('\n',name)
        with self.assertRaises(ValueError): exports.normalize_export_path('x','HTML')

    def test_text_and_markdown_export_only_current_chat_without_mutating_state(self):
        before=copy.deepcopy(self.state)
        for format in ('Text','Markdown'):
            path=exports.export_revision(self.root/'chat', 'Garden project', self.state, format)
            data=path.read_text(encoding='utf-8')
            for entry in self.state['messages']:self.assertIn(entry['content'],data)
            self.assertIn('Garden project',data);self.assertIn('Revision 1',data)
            self.assertNotIn('PRIVATE UNSENT DRAFT',data);self.assertNotIn('ARCHIVED SECRET',data)
            self.assertEqual(self.state,before)
        markdown=(self.root/'chat.md').read_text(encoding='utf-8')
        self.assertIn('### Distilled context',markdown);self.assertIn('### You',markdown)

    def test_empty_revision_all_formats(self):
        empty={'name':'Empty','messages':[]}
        for format in exports.EXTENSIONS:
            path=exports.export_revision(self.root/'empty','Project',empty,format)
            self.assertGreater(path.stat().st_size,0)

    def test_no_overwrite_and_confirmed_replace(self):
        target=self.root/'chat.txt';target.write_bytes(b'original')
        with self.assertRaises(FileExistsError):exports.export_revision(target,'P',self.state,'Text')
        self.assertEqual(target.read_bytes(),b'original')
        exports.export_revision(target,'P',self.state,'Text',overwrite=True)
        self.assertIn('Café',target.read_text(encoding='utf-8'))

    def test_race_creation_and_write_failure_preserve_existing(self):
        target=self.root/'chat.txt'
        link=os.link
        def race(src,dst):
            Path(dst).write_bytes(b'another writer')
            link(src,dst)
        with patch.object(exports.os,'link',side_effect=race):
            with self.assertRaises(FileExistsError):exports.export_revision(target,'P',self.state,'Text')
        self.assertEqual(target.read_bytes(),b'another writer')
        with patch.object(exports.os,'replace',side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):exports.export_revision(target,'P',self.state,'Text',overwrite=True)
        self.assertEqual(target.read_bytes(),b'another writer')
        self.assertFalse(list(self.root.glob('.hornet-export-*')))

    def test_pdf_failure_preserves_old_file(self):
        target=self.root/'chat.pdf';target.write_bytes(b'old PDF')
        with patch.object(exports,'_write_pdf',side_effect=OSError('render failed')):
            with self.assertRaises(OSError):exports.export_revision(target,'P',self.state,'PDF',overwrite=True)
        self.assertEqual(target.read_bytes(),b'old PDF')

    def test_export_symlink_rejected(self):
        target=self.root/'chat.pdf';target.write_bytes(b'old PDF')
        alias=self.root/'alias.pdf'
        try:
            alias.symlink_to(target)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows account cannot create symbolic links')
            raise
        with self.assertRaises(ValueError):exports.export_revision(alias,'P',self.state,'PDF',overwrite=True)
        self.assertEqual(target.read_bytes(),b'old PDF')

    @unittest.skipUnless(shutil.which('pdftotext') and shutil.which('pdfinfo'),'Poppler is needed for PDF content verification')
    def test_pdf_unicode_long_wrap_multipage_and_active_content(self):
        state=copy.deepcopy(self.state)
        state['messages'].append({'role':'assistant','content':'\n'.join('Row %03d: café and Greek αβγ. %s' % (i, 'Long wrapping phrase '*9) for i in range(55))+'\n'+'UNBROKEN-'+'X'*700+'-END\nFINAL MARKER'})
        path=exports.export_revision(self.root/'chat','Garden project',state,'PDF')
        data=path.read_bytes();self.assertTrue(data.startswith(b'%PDF-'))
        self.assertIn(b'/Subtype /Image',data)  # Color emoji must not disappear.
        text=subprocess.check_output(['pdftotext',str(path),'-']).decode()
        for expected in ('Garden project','Revision 1','Café résumé','Decisions: α + β = γ','First line','<script>not HTML</script>','FINAL MARKER'):
            self.assertIn(expected,text)
        self.assertNotIn('PRIVATE UNSENT DRAFT',text);self.assertNotIn('ARCHIVED SECRET',text)
        # Poppler dehyphenates wrapped words and includes page-number footers.
        self.assertEqual(text.count('X'),700)
        self.assertIn('-END',text)
        info=subprocess.check_output(['pdfinfo',str(path)]).decode()
        pages=int(next(line.split(':')[1] for line in info.splitlines() if line.startswith('Pages:')))
        self.assertGreater(pages,1)


if __name__=='__main__':unittest.main()
