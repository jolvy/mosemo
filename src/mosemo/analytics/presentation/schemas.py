import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from mosemo.observation.public import DetailedActivityContext, TimelineTimestamp
from mosemo.shared.domain.timezones import Timezone
from mosemo.shared.presentation.schemas import ApiResponseModel


class AnalysisBasisResponse(ApiResponseModel):
    """분석 결과를 계산한 기준입니다."""

    status: Literal["current"] = Field(
        description="현재 지표 정의와 계정 시간대로 계산한 결과임을 나타냅니다."
    )
    definition_version: int = Field(description="결과를 계산한 지표 정의 버전입니다.")
    timezone: Timezone = Field(description="날짜 경계에 쓴 계정 시간대입니다.")


class UsageTargetResponse(ApiResponseModel):
    """시간을 쓴 사이트 또는 앱입니다."""

    kind: Literal["site", "app"] = Field(
        description="사이트(eTLD+1)인지 앱(bundleId)인지 나타냅니다."
    )
    key: str = Field(
        description="비교 키입니다. 사이트는 IDNA ASCII 소문자, 앱은 bundleId입니다."
    )
    display_name: str = Field(
        description=(
            "화면 표시 이름입니다. 사이트는 유니코드 호스트, 앱은 그 기간 최신 관찰 "
            "이름이며 이름이 없으면 bundleId입니다."
        )
    )


class AppUsageResponse(ApiResponseModel):
    """앱 하나의 관찰 활동 시간입니다."""

    bundle_id: str = Field(description="앱 bundleId입니다.")
    display_name: str = Field(
        description="그 날짜에 관찰한 최신 앱 이름이며 없으면 bundleId입니다."
    )
    duration_us: int = Field(
        description="날짜와 겹친 관찰 활동 시간(마이크로초)입니다."
    )


class TargetUsageResponse(ApiResponseModel):
    """사용 대상 하나의 관찰 활동 시간입니다."""

    target: UsageTargetResponse = Field(description="사용 대상입니다.")
    duration_us: int = Field(
        description="날짜와 겹친 관찰 활동 시간(마이크로초)입니다."
    )


class UsageResponse(ApiResponseModel):
    """앱·사이트 사용 시간(C1)입니다. 순위는 시간 내림차순, 같으면 키 오름차순입니다."""

    apps: list[AppUsageResponse] = Field(description="앱 순위입니다.")
    targets: list[TargetUsageResponse] = Field(description="사용 대상 순위입니다.")
    unknown_app_us: int = Field(
        description="bundleId를 수집하지 못한 `앱 미확인` 시간(마이크로초)입니다."
    )
    opaque_us: int = Field(description="불투명 활동 시간(마이크로초)입니다.")
    observed_us: int = Field(
        description="날짜와 겹친 전체 관찰 활동 시간(마이크로초)입니다."
    )
    browser_without_url_us: int = Field(
        description="URL로 사이트를 정하지 못한 브라우저 앱 시간(마이크로초)입니다."
    )


class TargetTransitionResponse(ApiResponseModel):
    """맞닿은 두 구간의 사용 대상이 바뀐 전환입니다."""

    at: TimelineTimestamp = Field(description="다음 사용 대상 구간의 시작 시각입니다.")
    previous: UsageTargetResponse = Field(description="전환 전 사용 대상입니다.")
    next: UsageTargetResponse = Field(description="전환 뒤 사용 대상입니다.")
    previous_dwell_us: int = Field(
        description="전환 전 사용 대상에 이어서 머문 시간(마이크로초)입니다."
    )


class TargetTransitionsResponse(ApiResponseModel):
    """사용 대상 전환 목록입니다."""

    count: int = Field(description="전환 횟수입니다.")
    short_dwell_count: int = Field(
        description="앞 사용 대상에 5초 미만 머문 뒤의 전환 횟수입니다."
    )
    items: list[TargetTransitionResponse] = Field(description="시각순 전환입니다.")


class LabelTransitionResponse(ApiResponseModel):
    """맞닿은 두 구간의 유효한 확정 라벨이 바뀐 전환입니다."""

    at: TimelineTimestamp = Field(description="다음 라벨 구간의 시작 시각입니다.")
    previous_label_id: UUID = Field(description="전환 전 확정 라벨입니다.")
    next_label_id: UUID = Field(description="전환 뒤 확정 라벨입니다.")


class LabelTransitionsResponse(ApiResponseModel):
    """라벨 전환(C7)과 판정 대기 시간입니다."""

    count: int = Field(description="라벨 전환 횟수입니다.")
    pending_us: int = Field(
        description=(
            "유효한 확정이 없는 상세 활동 시간(마이크로초)입니다. 세션 분석에서는 "
            "세션 분석 시간 안의 판정 대기 시간입니다."
        )
    )
    items: list[LabelTransitionResponse] = Field(description="시각순 라벨 전환입니다.")


class CollectionResponse(ApiResponseModel):
    """수집 상태 보조 값입니다."""

    capture_gap_count: int = Field(description="날짜와 겹친 수집 공백 수입니다.")
    silence_count: int = Field(
        description="효과 끝이 날짜 안에 있는 활동 뒤 관찰 침묵 수입니다."
    )


class CompositionResponse(ApiResponseModel):
    """세션 분석 시간 구성(C4)입니다. 여섯 시간의 합이 `analysisUs`입니다."""

    analysis_us: int = Field(
        description="일시중지를 뺀 세션 분석 시간(마이크로초)입니다."
    )
    focus_us: int = Field(description="세션 라벨로 확정된 시간(마이크로초)입니다.")
    lapse_us: int = Field(description="다른 라벨로 확정된 이탈 시간(마이크로초)입니다.")
    unclassified_us: int = Field(description="미분류로 확정된 시간(마이크로초)입니다.")
    pending_us: int = Field(description="판정 대기 시간(마이크로초)입니다.")
    opaque_us: int = Field(description="불투명 활동 시간(마이크로초)입니다.")
    unobserved_us: int = Field(description="관찰이 없는 시간(마이크로초)입니다.")
    lapse_count: int = Field(description="집중 이탈 횟수입니다.")


class DistractionResponse(ApiResponseModel):
    """이탈 중 들어간 사용 대상(C5)입니다."""

    target: UsageTargetResponse | None = Field(
        description="사용 대상입니다. `앱 미확인` 활동이면 null입니다."
    )
    entries: int = Field(description="이탈 중 이 대상에 들어간 횟수입니다.")
    lapse_us: int = Field(description="이 대상에서 보낸 이탈 시간(마이크로초)입니다.")


class SessionTotalsResponse(ApiResponseModel):
    """그 날짜에 시작한 완료 세션의 합입니다."""

    count: int = Field(description="세션 수입니다.")
    composition: CompositionResponse = Field(description="세션 시간 구성의 합입니다.")
    distractions: list[DistractionResponse] = Field(
        description="이탈 시간 내림차순의 이탈 대상 합입니다."
    )


class DailyStatsResponse(ApiResponseModel):
    """계정 시간대 날짜 하나의 일간 통계입니다."""

    date: datetime.date = Field(description="계정 시간대 기준 날짜입니다.")
    observed_through: TimelineTimestamp | None = Field(
        description="결과의 기준 시각인 마지막 관찰 시각입니다. 관찰이 없으면 null입니다."
    )
    basis: AnalysisBasisResponse = Field(description="결과의 계산 기준입니다.")
    usage: UsageResponse = Field(description="앱·사이트 사용 시간입니다.")
    target_transitions: TargetTransitionsResponse = Field(
        description="날짜 안의 사용 대상 전환(C2)입니다."
    )
    label_transitions: LabelTransitionsResponse = Field(
        description="날짜 안의 라벨 전환(C7)입니다."
    )
    collection: CollectionResponse = Field(
        description="날짜의 수집 공백과 관찰 침묵 횟수입니다."
    )
    sessions: SessionTotalsResponse = Field(
        description="자정을 넘어도 시작한 날짜에만 더한 완료 세션의 합입니다."
    )


class PauseResponse(ApiResponseModel):
    """세션 분석에서 뺀 일시중지 구간 `[startedAt, endedAt)`입니다."""

    started_at: TimelineTimestamp = Field(description="일시중지 시작 시각입니다.")
    ended_at: TimelineTimestamp = Field(description="일시중지 종료 시각입니다.")


class LapseEntryResponse(ApiResponseModel):
    """이탈을 시작한 구간입니다."""

    target: UsageTargetResponse | None = Field(
        description="사용 대상입니다. `앱 미확인` 활동이면 null입니다."
    )
    label_id: UUID | None = Field(description="그 구간의 확정 라벨입니다.")
    context: DetailedActivityContext = Field(description="그 구간의 관찰 문맥입니다.")


class LapseResponse(ApiResponseModel):
    """집중 이탈 하나의 근거(C6)입니다."""

    started_at: TimelineTimestamp = Field(description="이탈 시작 시각입니다.")
    ended_at: TimelineTimestamp = Field(
        description="복귀 시각이며 복귀하지 않았으면 세션 분석 시간의 끝입니다."
    )
    entry: LapseEntryResponse = Field(
        description="이탈을 시작한 구간의 사용 대상, 확정 라벨, 관찰 문맥입니다."
    )
    previous_focus_target: UsageTargetResponse | None = Field(
        description=(
            "이탈 직전 집중 구간의 사용 대상입니다. 앞 집중 구간이 없거나 `앱 미확인`"
            "이면 null입니다."
        )
    )
    elapsed_us: int = Field(
        description="세션 시작부터 이탈까지의 세션 분석 시간(마이크로초)입니다."
    )
    lapse_us: int = Field(description="이탈 시간(마이크로초)입니다.")
    transition_count: int = Field(
        description="이탈 시작부터 복귀 전까지의 사용 대상 전환 수입니다."
    )
    returned: bool = Field(description="세션 라벨 활동으로 돌아왔는지 나타냅니다.")


class FocusSessionAnalysisResponse(ApiResponseModel):
    """완료한 집중 세션 하나의 분석입니다."""

    focus_session_id: UUID = Field(description="집중 세션 식별자입니다.")
    label_id: UUID = Field(description="세션 라벨입니다.")
    started_at: TimelineTimestamp = Field(description="세션 시작 시각입니다.")
    ended_at: TimelineTimestamp = Field(description="세션 종료 시각입니다.")
    has_pause_info: bool = Field(
        description="일시중지 정보가 있는지 나타냅니다. 없으면 시작~종료 전체를 분석합니다."
    )
    pauses: list[PauseResponse] = Field(description="일시중지 구간입니다.")
    observed_through: TimelineTimestamp | None = Field(
        description="결과의 기준 시각인 마지막 관찰 시각입니다. 관찰이 없으면 null입니다."
    )
    basis: AnalysisBasisResponse = Field(description="결과의 계산 기준입니다.")
    target_transitions: TargetTransitionsResponse = Field(
        description="세션 분석 시간 안의 사용 대상 전환(C3)입니다."
    )
    composition: CompositionResponse = Field(description="세션 시간 구성(C4)입니다.")
    distractions: list[DistractionResponse] = Field(
        description="이탈 시간 내림차순의 이탈 대상(C5)입니다."
    )
    lapses: list[LapseResponse] = Field(description="시각순 집중 이탈 근거(C6)입니다.")
    label_transitions: LabelTransitionsResponse = Field(
        description="세션 분석 시간 안의 라벨 전환(C7)입니다."
    )
