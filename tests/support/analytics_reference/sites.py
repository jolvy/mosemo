"""사용 대상의 사이트(eTLD+1) 계산.

https://publicsuffix.org/list/ 「Algorithm」을 그대로 따른다. ICANN·PRIVATE 절을 모두 규칙으로
쓰며 네트워크를 쓰지 않는다.
"""

import ipaddress
from urllib.parse import urlsplit

WEB_SCHEMES = frozenset({"http", "https"})
PUNYCODE_PREFIX = "xn--"


def normalize_host(host: str) -> str:
    """비교·출력용 호스트: 소문자, 끝의 점 하나 제거, `xn--` 레이블은 유니코드로 바꾼다."""
    labels = host.lower().removesuffix(".").split(".")
    return ".".join(decode_label(label) for label in labels).lower()


def decode_label(label: str) -> str:
    if not label.startswith(PUNYCODE_PREFIX):
        return label
    try:
        return label.removeprefix(PUNYCODE_PREFIX).encode("ascii").decode("punycode")
    except UnicodeError:
        return label


def web_host(url: str) -> str | None:
    """`http`·`https` URL의 정규화한 호스트(포트 제외). 그 밖의 URL이나 호스트가 없으면 None."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in WEB_SCHEMES or not host:
        return None
    return normalize_host(host) or None


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
                self.exceptions.append(tuple(normalize_host(rule[1:]).split(".")))
            else:
                self.rules.append(tuple(normalize_host(rule).split(".")))

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
