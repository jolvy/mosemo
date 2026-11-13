"""Public Suffix List 「Algorithm」과 독립성 규칙."""

import ast
import sys
import unicodedata
from pathlib import Path

import pytest

from tests.support.analytics_reference.sites import PublicSuffixList, web_host

PACKAGE = Path(__file__).resolve().parents[4] / "tests/support/analytics_reference"
RULES = PublicSuffixList(
    """// comment
com
*.ck
!www.ck
github.io
"""
)


@pytest.mark.parametrize(
    ("host", "site"),
    [
        ("m.youtube.com", "youtube.com"),
        ("a.b.c.com", "c.com"),
        ("com", "com"),
        ("a.b.ck", "a.b.ck"),
        ("b.ck", "b.ck"),
        ("www.ck", "www.ck"),
        ("a.www.ck", "www.ck"),
        ("user.github.io", "user.github.io"),
        ("x.user.github.io", "user.github.io"),
        ("example.unlisted", "example.unlisted"),
        ("a.example.unlisted", "example.unlisted"),
        ("localhost", "localhost"),
        ("192.168.0.1", "192.168.0.1"),
    ],
)
def test_site_follows_public_suffix_algorithm(host: str, site: str) -> None:
    assert RULES.site(host) == site


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://M.Example.com:443/a", "m.example.com"),
        ("http://intranet/wiki", "intranet"),
        ("chrome://settings", None),
        ("about:blank", None),
        ("file:///Users/a.pdf", None),
        ("http:///path", None),
        ("http://[::1/", None),
    ],
)
def test_web_host_accepts_only_http_urls_with_host(url: str, host: str | None) -> None:
    assert web_host(url) == host


def site_of(url: str, rules: PublicSuffixList = RULES) -> str | None:
    host = web_host(url)
    return None if host is None else rules.site(host)


@pytest.mark.parametrize(
    ("url", "site"),
    [
        ("about:blank", None),
        ("About:blank", None),
        ("mailto:", None),
        ("javascript:void(0)", None),
        ("data:text/html,hi", None),
        ("meet.google.com/x", "google.com"),
        ("localhost:8000/docs", "localhost"),
        ("192.168.0.1:8080/", "192.168.0.1"),
        ("example.com/?r=https://a.com", "example.com"),
    ],
)
def test_url_without_scheme_authority_is_non_web_or_gets_https(
    url: str, site: str | None
) -> None:
    assert site_of(url) == site


KOREAN = "예시.한국"
KOREAN_KEY = "xn--vv4b11d.xn--3e0b707e"


@pytest.mark.parametrize(
    ("url", "host"),
    [
        (f"https://{KOREAN}/", KOREAN_KEY),
        (f"https://{unicodedata.normalize('NFD', KOREAN)}/", KOREAN_KEY),
        (f"https://{KOREAN_KEY}/", KOREAN_KEY),
        ("https://ｇｉｔｈｕｂ.com/", "github.com"),
        ("https://ｇｉｔｈｕｂ.com．/", "github.com"),
        ("https://github.com./", "github.com"),
        ("https://github.com../", "github.com"),
        ("https://github.com．．/", "github.com"),
        ("https://github.com.．/", "github.com"),
        ("https://github.com。/", "github.com"),
        ("https://github.com\u2024/", "github.com"),
        ("https://github.com\ufe52/", "github.com"),
        ("https://github.com\u2024\u2024/", "github.com"),
        (f"https://{'A' * 64}.COM./", f"{'a' * 64}.com"),
        (f"https://{'A' * 64}.COM．/", f"{'a' * 64}.com"),
        (f"https://{'A' * 64}.COM\u2024/", f"{'a' * 64}.com\u2024"),
    ],
)
def test_site_key_is_lowercase_idna_ascii_without_trailing_dots(
    url: str, host: str
) -> None:
    assert web_host(url) == host


def test_public_suffix_rules_are_compared_by_site_key() -> None:
    rules = PublicSuffixList(f"{KOREAN}\n")

    assert site_of(f"https://a.b.{KOREAN}/", rules) == f"b.{KOREAN_KEY}"


def imported_modules(path: Path) -> list[tuple[int, str]]:
    """(상대 import 단계, 모듈 이름) 목록."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((0, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append((node.level, node.module or ""))
    return modules


def test_reference_package_imports_only_itself_and_standard_library() -> None:
    files = sorted(PACKAGE.glob("*.py"))
    violations = [
        f"{path.name}: {module}"
        for path in files
        for level, module in imported_modules(path)
        if not (
            level == 1
            or (
                level == 0
                and (
                    module == "tests.support.analytics_reference"
                    or module.startswith("tests.support.analytics_reference.")
                    or module.split(".")[0] in sys.stdlib_module_names
                )
            )
        )
    ]

    assert files
    assert violations == []
