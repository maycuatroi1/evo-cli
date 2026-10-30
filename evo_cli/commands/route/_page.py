"""What a page loads besides itself: a site is not open until its stylesheets and images are."""

from __future__ import annotations

import ipaddress
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

from evo_cli.commands.route import _probe

# Far past any <head>, and the rest of a long page repeats the same hosts.
MAX_PAGE = 2 * 1024 * 1024
WORKERS = 8
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.6 Safari/605.1.15"
)

# The <link> kinds a browser acts on by itself, unlike canonical or alternate.
LINK_RELS = {"stylesheet", "preload", "modulepreload", "preconnect", "dns-prefetch", "icon"}
SOURCE_TAGS = {"script", "img", "source", "video", "audio", "iframe"}

# Under a country code these are a public suffix, not a site: example.com.vn, example.co.uk.
COUNTRY_SECOND_LEVEL = {"ac", "co", "com", "edu", "gov", "net", "org"}


class _AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "link":
            if LINK_RELS & set((fields.get("rel") or "").lower().split()):
                self.urls.append(fields.get("href"))
        elif tag in SOURCE_TAGS:
            self.urls.append(fields.get("src"))
            self.urls.extend(part.split()[0] for part in (fields.get("srcset") or "").split(",") if part.strip())


def _is_ip(name):
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def asset_hosts(html, base_url):
    """The names a browser would connect to for this page, ignoring what only a click would reach."""
    parser = _AssetParser()
    parser.feed(html)
    hosts = []
    for url in parser.urls:
        try:
            name = urllib.parse.urlsplit(urllib.parse.urljoin(base_url, url or "")).hostname
        except ValueError:
            continue
        if name and "." in name and not _is_ip(name) and name not in hosts:
            hosts.append(name)
    return hosts


def fetch(host, proxy=None, timeout=6.0):
    """Read the front page of ``host``, through ``proxy`` when there is one; returns where it ended up and the HTML."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
    request = urllib.request.Request(f"https://{host}/", headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with opener.open(request, timeout=timeout) as response:
        return response.geturl(), response.read(MAX_PAGE).decode("utf-8", "replace")


def registrable(host):
    labels = host.split(".")
    keep = 3 if len(labels[-1]) == 2 and labels[-2] in COUNTRY_SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def covers(opened, host):
    return any(host == name or host.endswith(f".{name}") for name in opened)


def blocked_hosts(host, proxy, opened, timeout=6.0):
    """The hosts the page at ``host`` needs that a browser cannot reach, leaving out what ``opened`` already covers.

    The site's own family is in the running because a page calls its API and sign-in
    hosts from script, where no tag shows them.
    """
    final_url, html = fetch(host, proxy=proxy, timeout=timeout)
    names = dict.fromkeys([*asset_hosts(html, final_url), registrable(host)])
    names = [name for name in names if not covers([host, *opened], name)]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        alive = list(pool.map(lambda name: _probe.reachable(name, timeout=timeout), names))
    return [name for name, ok in zip(names, alive) if not ok]
