"""DF-5: 같은 생성 입력에서 제품 분석 계산 모듈 = 저장소 참조 구현(AC-8001~8011, AC-8017~8022, E2)."""

from hypothesis import given

from tests.support import analytics_product, analytics_reference
from tests.support.analytics_strategies import PSL, GeneratedTimeline, timelines
from tests.support.analytics_timelines import only


@given(timelines())
def test_daily_stats_match_reference(timeline: GeneratedTimeline) -> None:
    for day in timeline.days:
        assert analytics_product.daily(
            timeline.data, day, PSL
        ) == analytics_reference.daily(timeline.data, day, PSL), day


@given(timelines())
def test_session_analysis_matches_reference(timeline: GeneratedTimeline) -> None:
    for session_id in timeline.session_ids:
        assert analytics_product.session(
            timeline.data, session_id, PSL
        ) == analytics_reference.session(timeline.data, session_id, PSL), session_id


@given(timelines())
def test_day_reading_matches_reference(timeline: GeneratedTimeline) -> None:
    for day in timeline.days:
        assert analytics_product.read_day(
            timeline.data, day, PSL
        ) == analytics_reference.read_day(timeline.data, day, PSL), day


@given(timelines())
def test_daily_stats_from_selected_segments_equal_full_calculation(
    timeline: GeneratedTimeline,
) -> None:
    data = timeline.data
    for day in timeline.days:
        chosen = only(data, analytics_product.read_day(data, day, PSL))
        assert analytics_product.daily(chosen, day, PSL) == analytics_product.daily(
            data, day, PSL
        ), day
