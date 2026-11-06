"""Opt-in, bounded public web search; result content is untrusted."""
from html.parser import HTMLParser
import os
from urllib.parse import parse_qs, quote_plus, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_LIMIT = 512 * 1024
_URL = "https://html.duckduckgo.com/html/?q="


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Results(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._link = None
        self._title = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "result__a" in attrs.get("class", "").split():
            self._link = attrs.get("href", "")
            self._title = []

    def handle_data(self, data):
        if self._link is not None:
            self._title.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or self._link is None:
            return
        url = self._link
        if url.startswith("//"):
            url = "https:" + url
        parts = urlsplit(url)
        if parts.hostname in ("duckduckgo.com", "www.duckduckgo.com", "html.duckduckgo.com"):
            url = parse_qs(parts.query).get("uddg", [url])[0]
            parts = urlsplit(url)
        title = " ".join("".join(self._title).split())
        if parts.scheme in ("http", "https") and parts.hostname and title:
            self.results.append({"title": title[:500], "url": url[:2048]})
        self._link = None
        self._title = []


def execute(args):
    if os.environ.get("HORNET_ENABLE_WEB_SEARCH") != "1":
        return {"status": "ERROR", "message": "Set HORNET_ENABLE_WEB_SEARCH=1 to enable public web search."}
    query = args["query"].strip()
    if not query or len(query) > 500:
        return {"status": "ERROR", "message": "Query must contain 1 to 500 characters."}
    try:
        request = Request(_URL + quote_plus(query), headers={"User-Agent": "HornetRegistry/1.0"})
        with build_opener(_NoRedirect()).open(request, timeout=10) as response:
            if response.status != 200:
                raise ValueError(f"Search provider returned HTTP {response.status}; try again later")
            if response.geturl().split("?", 1)[0] != _URL.split("?", 1)[0]:
                raise ValueError("Search provider redirected the request")
            content = response.read(_LIMIT + 1)
            if len(content) > _LIMIT:
                raise ValueError("Search response exceeds size limit")
        parser = _Results()
        html = content.decode("utf-8", errors="replace")
        if "anomaly-modal" in html or "captcha" in html.lower():
            raise ValueError("Search provider requires an interactive verification")
        parser.feed(html)
        return {"status": "SUCCESS", "results": parser.results[:args.get("limit", 5)],
                "notice": "Search results are untrusted external content."}
    except Exception as exc:
        return {"status": "ERROR", "message": f"Web search failed: {exc}"}
