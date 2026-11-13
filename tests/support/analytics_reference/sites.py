"""사용 대상의 사이트(eTLD+1) 계산.

https://publicsuffix.org/list/ 「Algorithm」을 그대로 따른다. ICANN·PRIVATE 절을 모두 규칙으로
쓰며 네트워크를 쓰지 않는다.
"""

import ipaddress
import re
from urllib.parse import urlsplit

WEB_SCHEMES = frozenset({"http", "https"})
SCHEME_WITH_AUTHORITY = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
# scheme 뒤 첫 글자가 숫자가 아니면 비웹이다(`localhost:8000`은 호스트와 포트).
NON_WEB_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:(?![0-9])")
# `idna` 코덱이 레이블 구분자로 읽는 점(ASCII·표의 문자·전각·반각).
TRAILING_DOTS = re.compile(r"[.\u3002\uff0e\uff61]+\Z")


def site_key(host: str) -> str:
    """사이트 비교·출력 키(D12): IDNA 2003 ASCII 소문자, 끝의 점은 모두 지운다.

    IDNA 변환이 실패하면 소문자로 바꾸고 끝 점을 지운 호스트 그대로다.
    """
    host = TRAILING_DOTS.sub("", host)
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        ascii_host = host
    return ascii_host.lower().rstrip(".")


def web_url(url: str) -> str | None:
    """D7을 적용할 URL(D9). scheme 없는 비웹 URL이면 None이다.

    scheme 바로 뒤에 `://`가 있으면 그대로, `scheme:` 뒤 첫 글자가 숫자가 아니면 비웹,
    그 밖은 앞에 `https://`를 붙인다.
    """
    if SCHEME_WITH_AUTHORITY.match(url):
        return url
    if NON_WEB_SCHEME.match(url):
        return None
    return f"https://{url}"


def web_host(url: str) -> str | None:
    """`http`·`https` URL의 사이트 키 형식 호스트(포트 제외). 그 밖의 URL이나 호스트가 없으면 None."""
    target = web_url(url)
    if target is None:
        return None
    try:
        parts = urlsplit(target)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in WEB_SCHEMES or not host:
        return None
    return site_key(host) or None


class PublicSuffixList:
    """Public Suffix List 텍스트에서 읽은 규칙으로 사이트를 정한다."""

    def __init__(self, text: str) -> None:
        self.rules: list[tuple[str, ...]] = []
        self.exceptions: list[tuple[str, ...]] = []
        for line in text.splitlines():
            words = line.split()
            if not words or words[0].startswith("//"):
                continue
            rule = words[0]
            if rule.startswith("!"):
                self.exceptions.append(tuple(site_key(rule[1:]).split(".")))
            else:
                self.rules.append(tuple(site_key(rule).split(".")))

    def site(self, host: str) -> str:
        """eTLD+1. 정할 수 없으면(IP 주소, 공개 접미사 자체, 빈 레이블) 호스트 그대로다(D7)."""
        if is_ip_address(host):
            return host
        labels = tuple(host.split("."))
        if not all(labels):
            return host
        suffix = self.public_suffix_length(labels)
        if suffix >= len(labels):
            return host
        return ".".join(labels[-(suffix + 1) :])

    def public_suffix_length(self, labels: tuple[str, ...]) -> int:
        exceptions = [rule for rule in self.exceptions if matches(rule, labels)]
        if exceptions:
            return max(len(rule) for rule in exceptions) - 1
        matched = [len(rule) for rule in self.rules if matches(rule, labels)]
        return max(matched, default=1)


def matches(rule: tuple[str, ...], labels: tuple[str, ...]) -> bool:
    if len(rule) > len(labels):
        return False
    tail = labels[len(labels) - len(rule) :]
    return all(part in {"*", label} for part, label in zip(rule, tail, strict=True))


def is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
