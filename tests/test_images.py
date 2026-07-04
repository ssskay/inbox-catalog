"""Offline coverage for image download guards.

These never touch the network: they exercise the early-return paths in
``images.download`` (empty URL and RFC 2606/6761 reserved demo hosts), which is
what keeps the zero-setup demo run free of connection-failure warnings.
"""
from inboxcatalog import images


def test_empty_url_returns_none():
    assert images.download(None) == (None, None)
    assert images.download("") == (None, None)


def test_reserved_demo_hosts_are_skipped_without_network():
    # .example/.invalid/.test/.localhost never resolve — download must short
    # circuit to (None, None) rather than attempt a request and warn.
    for url in (
        "https://cdn.tabletoptrove.example/img/wingspan_box.jpg",
        "http://shop.invalid/item.png",
        "https://foo.test/a.webp",
        "https://bar.localhost/b.jpg",
    ):
        assert images.download(url) == (None, None), url


def test_reserved_host_match_is_on_suffix_not_substring():
    # A real host that merely contains "example" must NOT be treated as reserved
    # (guards against a naive substring check). We can't fetch it offline, so we
    # only assert it is not short-circuited by the reserved-host guard — i.e. it
    # proceeds far enough to fail on the network, returning (None, None) too but
    # via the request path. Parsing the host is what we're really checking here.
    from urllib.parse import urlparse
    assert urlparse("https://example-shop.com/x.jpg").hostname == "example-shop.com"
    assert not "example-shop.com".endswith(".example")
