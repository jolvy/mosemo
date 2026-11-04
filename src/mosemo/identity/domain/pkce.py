import base64
import hashlib
import re

PKCE_CODE_CHALLENGE_PATTERN = r"^[A-Za-z0-9_-]{43}$"
PKCE_CODE_CHALLENGE_RE = re.compile(PKCE_CODE_CHALLENGE_PATTERN)
PKCE_CODE_VERIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


def is_valid_code_challenge(code_challenge: str) -> bool:
    return PKCE_CODE_CHALLENGE_RE.fullmatch(code_challenge) is not None


def create_code_challenge(code_verifier: str) -> str:
    if PKCE_CODE_VERIFIER_PATTERN.fullmatch(code_verifier) is None:
        raise ValueError("Invalid PKCE code verifier")

    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
