"""Usage targets: the app or site a segment was spent on (metrics spec C1, D7, D9, D12)."""

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar
from urllib.parse import urlsplit

from mosemo.analytics.domain.source import SourceSegment

WEB_SCHEMES = frozenset({"http", "https"})
# D9: a scheme directly followed by `://` is a URL D7 applies to as is.
SCHEME_WITH_AUTHORITY = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
# D9: `scheme:` not followed by an ASCII digit is a non-web URL (`localhost:8000` is
# a host and a port).
NON_WEB_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:(?![0-9])")
# D12: dots the IDNA 2003 codec reads as label separators.
LABEL_SEPARATORS = ".。．｡"


class UsageTargetKind(StrEnum):
    SITE = "site"
    APP = "app"


@dataclass(frozen=True, kw_only=True)
class UsageTarget:
    """A site (eTLD+1 key) or an app (bundleId). Compared and stored by key only."""

    kind: UsageTargetKind = field(compare=False)
    key: str

    def display_name(self) -> str:
        """The site key in Unicode for the screen (D12); an app shows its bundleId."""
        if self.kind is UsageTargetKind.APP:
            return self.key
        try:
            return self.key.encode("ascii").decode("idna")
        except UnicodeError:
            return self.key


@dataclass(frozen=True)
class UnknownApp:
    """The single `앱 미확인` entry that groups activity without a bundleId (FR-3, D4)."""

    key: ClassVar[str] = "앱 미확인"


UNKNOWN_APP = UnknownApp()

# What C5 and C6 attribute a lapse piece to.
LapseTarget = UsageTarget | UnknownApp


def site_key(host: str) -> str:
    """D12: IDNA 2003 ASCII in lowercase without trailing dots.

    A host the codec cannot convert keeps its lowercased form.
    """
    host = host.rstrip(LABEL_SEPARATORS)
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        ascii_host = host
    return ascii_host.lower().rstrip(".")


def web_host(url: str) -> str | None:
    """The site-key form of an `http`/`https` URL host without its port (D7, D9)."""
    if NON_WEB_SCHEME.match(url) and not SCHEME_WITH_AUTHORITY.match(url):
        return None
    target = url if SCHEME_WITH_AUTHORITY.match(url) else f"https://{url}"
    try:
        parts = urlsplit(target)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in WEB_SCHEMES or not host:
        return None
    return site_key(host) or None


def is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class PublicSuffixRules:
    """Public Suffix List rules (https://publicsuffix.org/list/ 「Algorithm」)."""

    def __init__(self, text: str) -> None:
        rules: set[tuple[str, ...]] = set()
        exceptions: set[tuple[str, ...]] = set()
        for line in text.splitlines():
            words = line.split()
            if not words or words[0].startswith("//"):
                continue
            rule = words[0]
            if rule.startswith("!"):
                exceptions.add(tuple(site_key(rule[1:]).split(".")))
            else:
                rules.add(tuple(site_key(rule).split(".")))
        self._rules = frozenset(rules)
        self._exceptions = frozenset(exceptions)
        # No rule matches a tail longer than this, so longer tails are not built.
        self._depth = max(map(len, rules | exceptions), default=1)

    def site(self, host: str) -> str:
        """eTLD+1, or the host itself when there is none (IP, a public suffix, D7)."""
        if is_ip_address(host):
            return host
        labels = tuple(host.split("."))
        if not all(labels):
            return host
        suffix = self._public_suffix_length(labels)
        if suffix >= len(labels):
            return host
        return ".".join(labels[-(suffix + 1) :])

    def _public_suffix_length(self, labels: tuple[str, ...]) -> int:
        depth = min(len(labels), self._depth)
        tails = [labels[len(labels) - size :] for size in range(1, depth + 1)]
        exceptions = [len(tail) for tail in tails if tail in self._exceptions]
        if exceptions:
            return max(exceptions) - 1
        matched = [
            len(tail)
            for tail in tails
            if tail in self._rules or ("*", *tail[1:]) in self._rules
        ]
        return max(matched, default=1)


class UsageTargetResolver:
    """Resolves the app and the usage target of a segment from its context.

    Opaque activity, unknown apps and capture gaps have no usage target. The PSL
    rules text comes from the assembly root (`analytics.bootstrap`).
    """

    def __init__(self, public_suffix_rules: str) -> None:
        self._sites = PublicSuffixRules(public_suffix_rules)

    def app(self, segment: SourceSegment) -> str | None:
        """The captured bundleId of a detailed activity; None for an unknown app."""
        if not segment.is_detailed_activity:
            return None
        bundle_id = section(section(segment.context, "app"), "bundleId")
        value = bundle_id.get("value")
        if bundle_id.get("status") != "captured" or not isinstance(value, str):
            return None
        return value

    def target(self, segment: SourceSegment) -> UsageTarget | None:
        app = self.app(segment)
        if app is None:
            return None
        host = self._browser_host(segment)
        if host is None:
            return UsageTarget(kind=UsageTargetKind.APP, key=app)
        return UsageTarget(kind=UsageTargetKind.SITE, key=self._sites.site(host))

    def lapse_target(self, segment: SourceSegment) -> LapseTarget:
        """The usage target, or `앱 미확인` for activity without one (D4)."""
        return self.target(segment) or UNKNOWN_APP

    def is_browser_app(self, segment: SourceSegment) -> bool:
        """`web.kind = browser` whose URL names no site (P7 browser app time)."""
        return (
            self.app(segment) is not None
            and is_browser(segment)
            and self._browser_host(segment) is None
        )

    def _browser_host(self, segment: SourceSegment) -> str | None:
        if not is_browser(segment):
            return None
        url = section(section(segment.context, "web"), "url")
        value = url.get("value")
        if url.get("status") != "captured" or not isinstance(value, str):
            return None
        return web_host(value)


def is_browser(segment: SourceSegment) -> bool:
    return section(segment.context, "web").get("kind") == "browser"


def section(value: Mapping[str, object] | None, name: str) -> Mapping[str, object]:
    """A nested context object, or empty when it is missing or not an object."""
    inner = (value or {}).get(name)
    return inner if isinstance(inner, Mapping) else {}
