"""사용 대상(D7·D9·D12)과 PSL 규칙, 묶어 둔 PSL 스냅샷과 지표 정의."""

import hashlib
from importlib.resources import files

import pytest

from mosemo.analytics import bootstrap
from mosemo.analytics.domain.definition import CURRENT_DEFINITION
from mosemo.analytics.domain.usage_targets import (
    UNKNOWN_APP,
    PublicSuffixRules,
    UsageTarget,
    UsageTargetKind,
    UsageTargetResolver,
    site_key,
    web_host,
)
from tests.support import analytics_product, analytics_reference
from tests.support.analytics_product import segment_of
from tests.support.analytics_strategies import PSL
from tests.support.analytics_timelines import (
    DAY,
    OPAQUE,
    SESSION,
    activity,
    app,
    browser,
    focus,
    gap,
    timeline,
    unknown_app,
)
from tests.support.analytics_timelines import PSL as TIMELINE_PSL

# 정의 version마다 묶은 PSL 스냅샷의 sha256. PSL 파일을 바꾸면 사이트 키가 바뀌므로
# 새 version과 그 sha256을 여기에 더하고 `CURRENT_DEFINITION`을 올린다.
PSL_SNAPSHOT_BY_VERSION = {
    1: "db4ccf1f59d72022396cfe7c9ed3e107a927fce3532b4cd6eeb6f8a9dc525f05",
}


def site(key: str) -> UsageTarget:
    return UsageTarget(kind=UsageTargetKind.SITE, key=key)


def bundle(key: str) -> UsageTarget:
    return UsageTarget(kind=UsageTargetKind.APP, key=key)


def test_bundled_psl_is_read_as_a_package_resource() -> None:
    rules = bootstrap.public_suffix_rules()

    assert rules.startswith("// This Source Code Form is subject to the terms of")
    assert "Mozilla Public" in rules.splitlines()[0] + rules.splitlines()[1]
    assert "\ngithub.io\n" in rules


def test_current_definition_pins_the_bundled_psl_snapshot() -> None:
    data = (
        files("mosemo.analytics.domain")
        .joinpath(bootstrap.PUBLIC_SUFFIX_LIST)
        .read_bytes()
    )

    assert hashlib.sha256(data).hexdigest() == CURRENT_DEFINITION.psl_snapshot
    assert PSL_SNAPSHOT_BY_VERSION[CURRENT_DEFINITION.version] == (
        CURRENT_DEFINITION.psl_snapshot
    )


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("m.youtube.com", "youtube.com"),
        ("foo.github.io", "foo.github.io"),
        ("github.io", "github.io"),
        ("a.b.co.kr", "b.co.kr"),
        ("www.city.kawasaki.jp", "city.kawasaki.jp"),
        ("a.b.kawasaki.jp", "a.b.kawasaki.jp"),
        ("intranet", "intranet"),
        ("192.168.0.1", "192.168.0.1"),
        ("xn--vv4b11d.xn--3e0b707e", "xn--vv4b11d.xn--3e0b707e"),
    ],
)
def test_bundled_psl_gives_etld_plus_one(host: str, expected: str) -> None:
    assert PublicSuffixRules(bootstrap.public_suffix_rules()).site(host) == expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.ck", "www.ck"),
        ("a.b.ck", "a.b.ck"),
        ("x.a.b.ck", "a.b.ck"),
        ("a..com", "a..com"),
        ("co.kr", "co.kr"),
        ("::1", "::1"),
        ("example.unknown", "example.unknown"),
    ],
)
def test_wildcard_exception_and_undecidable_hosts(host: str, expected: str) -> None:
    assert PublicSuffixRules(PSL).site(host) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://M.YouTube.COM./x", "m.youtube.com"),
        ("meet.google.com/x", "meet.google.com"),
        ("localhost:8000/docs", "localhost"),
        ("http://[::1]:3000/", "::1"),
        ("About:blank", None),
        ("mailto:", None),
        ("foo:１２/x", None),
        ("chrome://settings", None),
        ("file:///Users/a.pdf", None),
        ("http://[::1/", None),
        ("https://github.com.。/", "github.com"),
    ],
)
def test_web_host(url: str, expected: str | None) -> None:
    assert web_host(url) == expected


def test_resolver_targets() -> None:
    resolver = UsageTargetResolver(PSL)
    segments = {
        "app": segment_of(activity("s1", 0, 1, app("A"))),
        "site": segment_of(activity("s2", 0, 1, browser("https://m.youtube.com/"))),
        "browser": segment_of(activity("s3", 0, 1, browser(None))),
        "unknown": segment_of(activity("s4", 0, 1, unknown_app())),
        "opaque": segment_of(activity("s5", 0, 1, OPAQUE)),
        "gap": segment_of(gap("g1", 0, 1)),
    }

    assert {name: resolver.target(s) for name, s in segments.items()} == {
        "app": bundle("A"),
        "site": site("youtube.com"),
        "browser": bundle("com.google.Chrome"),
        "unknown": None,
        "opaque": None,
        "gap": None,
    }
    assert [name for name, s in segments.items() if resolver.is_browser_app(s)] == [
        "browser"
    ]
    assert resolver.lapse_target(segments["unknown"]) is UNKNOWN_APP
    assert resolver.lapse_target(segments["app"]) == bundle("A")


def test_resolver_ignores_malformed_context_values() -> None:
    resolver = UsageTargetResolver(PSL)
    context = app("A") | {"web": {"kind": "browser", "url": {"status": "captured"}}}
    context["app"] = {"bundleId": {"status": "captured", "value": 3}}
    segment = segment_of(activity("s1", 0, 1, context))
    no_url = segment_of(
        activity("s2", 0, 1, browser(None) | {"web": {"kind": "browser", "url": "x"}})
    )

    assert resolver.app(segment) is None
    assert resolver.target(no_url) == bundle("com.google.Chrome")


def test_display_name_shows_sites_in_unicode() -> None:
    assert site("xn--vv4b11d.xn--3e0b707e").display_name() == "예시.한국"
    assert site("xn--zz.com").display_name() == "xn--zz.com"
    assert site(f"{'a' * 64}.com").display_name() == f"{'a' * 64}.com"
    assert bundle("com.google.Chrome").display_name() == "com.google.Chrome"


def test_targets_compare_by_key_only() -> None:
    assert site("a.com") == site("a.com")
    assert site("a.com") == bundle("a.com")
    assert hash(site("a.com")) == hash(bundle("a.com"))
    assert site("a.com") != site("b.com")
    assert UNKNOWN_APP.key == "앱 미확인"


def test_app_and_site_with_the_same_key_are_one_target() -> None:
    data = timeline(
        activity("s1", 0, 10, app("notion.id")),
        activity("s2", 10, 20, browser("https://notion.id/x")),
        activity("s3", 20, 30, app("notion.id")),
        sessions=[focus(start=0, end=30)],
    )

    assert analytics_product.daily(data, DAY, TIMELINE_PSL) == (
        analytics_reference.daily(data, DAY, TIMELINE_PSL)
    )
    assert analytics_product.session(data, SESSION, TIMELINE_PSL) == (
        analytics_reference.session(data, SESSION, TIMELINE_PSL)
    )


def test_long_hosts_resolve_without_scanning_every_tail() -> None:
    labels = 50_000
    many_labels = ".".join(["a"] * labels) + ".b.co.kr"

    assert site_key("github.com" + "." * 20_000) == "github.com"
    assert PublicSuffixRules(bootstrap.public_suffix_rules()).site(many_labels) == (
        "b.co.kr"
    )
