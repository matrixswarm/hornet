import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hornet.registry.proxy_resolver import ProxyResolver
from hornet.registry.registry_manager import RegistryManager


class AtomicPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.resolver=ProxyResolver(registry=RegistryManager(allowed_uris=['files.text.atomic_edit']))

    def call(self,**args):
        return self.resolver.execute('files.text.atomic_edit',args)

    def test_create_is_hash_guarded_and_refuses_overwrite(self):
        target=self.root/'file.py'; empty=hashlib.sha256(b'').hexdigest()
        missing=self.call(path=str(target),action='create',new_text='VALUE = 1\n',commit=True)
        self.assertEqual(missing['status'],'ERROR')
        created=self.call(path=str(target),action='create',new_text='VALUE = 1\n',expected_sha256=empty,commit=True)
        self.assertEqual(created['status'],'SUCCESS')
        actual=hashlib.sha256(target.read_bytes()).hexdigest()
        overwrite=self.call(path=str(target),action='create',new_text='VALUE = 2\n',expected_sha256=actual,commit=True)
        self.assertEqual(overwrite['status'],'ERROR')
        self.assertEqual(target.read_text(),'VALUE = 1\n')

    def test_multiline_replacement_requires_unique_block_and_valid_python(self):
        target=self.root/'file.py'; target.write_text('def calc(x):\n    value = x\n    return value\n')
        before=hashlib.sha256(target.read_bytes()).hexdigest()
        bad=self.call(path=str(target),action='replace',target_text='    value = x\n    return value',new_text='    if:',expected_sha256=before,commit=True)
        self.assertEqual(bad['status'],'ERROR')
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)
        good=self.call(path=str(target),action='replace',target_text='    value = x\n    return value',new_text='    value = x * x\n    return value',expected_sha256=before,commit=True)
        self.assertEqual(good['status'],'SUCCESS')
        self.assertIn('value = x * x',target.read_text())
        target.write_text('a = 1\nb = 2\na = 1\nb = 2\n')
        digest=hashlib.sha256(target.read_bytes()).hexdigest()
        duplicate=self.call(path=str(target),action='replace',target_text='a = 1\nb = 2',new_text='c = 3',expected_sha256=digest,commit=True)
        self.assertEqual(duplicate['status'],'ERROR')
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), digest)

    def test_delete_is_guarded_and_rejects_ambiguous_or_stale_edits(self):
        target=self.root/'file.py'; target.write_text('VALUE = 1\nVALUE = 2\n')
        before=hashlib.sha256(target.read_bytes()).hexdigest()
        bad=self.call(path=str(target),action='delete',target_text='VALUE',new_text='',expected_sha256=before,commit=True)
        self.assertEqual(bad['status'],'ERROR'); self.assertEqual(target.read_text(),'VALUE = 1\nVALUE = 2\n')
        good=self.call(path=str(target),action='delete',target_text='VALUE = 2',new_text='',expected_sha256=before,commit=True)
        self.assertEqual(good['status'],'SUCCESS'); self.assertEqual(target.read_text(),'VALUE = 1\n')
        stale=self.call(path=str(target),action='delete',target_text='VALUE = 1',new_text='',expected_sha256=before,commit=True)
        self.assertEqual(stale['status'],'ERROR'); self.assertEqual(target.read_text(),'VALUE = 1\n')

    def test_lf_model_blocks_match_and_preserve_crlf_source(self):
        target=self.root/'file.py'; target.write_bytes(b'a = 1\r\nb = 2\r\n')
        digest=hashlib.sha256(target.read_bytes()).hexdigest()
        result=self.call(path=str(target),action='replace',target_text='a = 1\nb = 2',new_text='a = 3\nb = 4',expected_sha256=digest,commit=True)
        self.assertEqual(result['status'],'SUCCESS')
        self.assertEqual(target.read_bytes(),b'a = 3\r\nb = 4\r\n')
        target.write_bytes(b'a = 1\r\nb = 2\r\na = 1\nb = 2\n')
        original=target.read_bytes(); digest=hashlib.sha256(original).hexdigest()
        result=self.call(path=str(target),action='replace',target_text='a = 1\nb = 2',new_text='a = 3\nb = 4',expected_sha256=digest,commit=True)
        self.assertEqual(result['status'],'ERROR')
        self.assertEqual(target.read_bytes(),original)


if __name__=='__main__': unittest.main()
