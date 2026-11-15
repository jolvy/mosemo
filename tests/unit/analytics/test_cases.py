"""SC-7: 제품 분석 계산 모듈이 지표 명세 사례 98개의 문서 기대값을 낸다."""

import pytest

from tests.support import analytics_product
from tests.support.analytics_cases import CASES, case_failures, spec_case_ids


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_product_matches_documented_case(case_id: str) -> None:
    assert case_failures(case_id, analytics_product) == []


def test_product_cases_cover_every_spec_case_id() -> None:
    assert set(CASES) == spec_case_ids()
