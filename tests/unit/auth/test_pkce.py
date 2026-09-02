import pytest

from mosemo.auth.pkce import create_code_challenge, is_valid_code_challenge


def test_create_code_challenge_uses_s256() -> None:
    assert create_code_challenge("A" * 43) == (
        "DwBzhbb51LfusnSGBa_hqYSgo7-j8BTQnip4TOnlzRo"
    )
    assert is_valid_code_challenge(create_code_challenge("A" * 43))


@pytest.mark.parametrize(
    "code_verifier",
    ["short", "A" * 129, "A" * 42 + "!"],
)
def test_create_code_challenge_rejects_invalid_verifier(
    code_verifier: str,
) -> None:
    with pytest.raises(ValueError):
        create_code_challenge(code_verifier)
