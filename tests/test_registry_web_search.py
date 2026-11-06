import io
import os
import unittest
from unittest.mock import patch

from hornet.registry.assets import web_search
from hornet.registry.registry_manager import RegistryManager


class Response(io.BytesIO):
    status = 200
    def geturl(self):
        return 'https://html.duckduckgo.com/html/?q=example'


class WebSearchTests(unittest.TestCase):
    def test_requires_explicit_catalog_and_environment_opt_in(self):
        self.assertNotIn('research.web.search', RegistryManager().assets)
        self.assertIn('research.web.search', RegistryManager(allowed_uris=['research.web.search']).assets)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(web_search.execute({'query': 'example'})['status'], 'ERROR')

    def test_parses_bounded_public_links_without_following_redirects(self):
        page = (b'<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa">'
                b'Example <b>title</b></a><a class="result__a" href="javascript:bad">Bad</a>')
        with patch.dict(os.environ, {'HORNET_ENABLE_WEB_SEARCH': '1'}), patch.object(web_search, 'build_opener') as opener:
            opener.return_value.open.return_value = Response(page)
            result = web_search.execute({'query': 'example', 'limit': 3})
            self.assertEqual(result['results'], [{'title': 'Example title', 'url': 'https://example.org/a'}])
            self.assertEqual(result['status'], 'SUCCESS')
            self.assertIsNone(web_search._NoRedirect().redirect_request(None, None, 302, '', {}, 'http://localhost/'))
            opener.return_value.open.return_value = Response(b'x' * (web_search._LIMIT + 1))
            self.assertEqual(web_search.execute({'query': 'example'})['status'], 'ERROR')
            opener.return_value.open.return_value = Response(b'<div class="anomaly-modal">challenge</div>')
            self.assertEqual(web_search.execute({'query': 'example'})['status'], 'ERROR')


if __name__ == '__main__':
    unittest.main()
