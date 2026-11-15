"""F-2 2차: 결정 (a)의 표시 대상이 "모든 날짜"와 실제로 갈리는 경우를 센다(protocol_model_tz3.py를 가져다 씀).

A0(대상 = (저장 ∪ 표시) ±1일)의 도달 가능한 모든 상태 중, 시간대 변경 커밋 직전 상태(그 다음 전이가
커밋)에서 (a)의 대상과 "모든 날짜"가 다른 상태 수와 빠지는 날짜, 한 예를 출력한다. A0는 (a)대로
탐색하므로, 빠진 날짜가 있어도 A0가 I1~I9를 통과하면 그 차이는 정확성에 영향이 없는 것이다(L3E 제외).

실행(저장소 루트에서):
  python -m tools.analytics_protocol_models.protocol_model_tz3_distinct          # tz3의 모든 시나리오
  python -m tools.analytics_protocol_models.protocol_model_tz3_distinct --small  # tz3의 작은 시나리오
"""

import sys
from collections import Counter

from tools.analytics_protocol_models import protocol_model_tz3 as m


def main(argv: list[str]) -> int:
    v = next(x for x in m.VARIANTS if x.name == "A0")
    scenarios = m.SMALL_SCENARIOS if "--small" in argv else m.SCENARIOS
    for sc in scenarios:
        r = m.explore(v, sc, keep_parents=True)
        assert r.parents is not None
        commits = 0
        differ = 0
        missing: Counter[tuple[tuple[str, ...], str]] = Counter()
        example = None
        for s in r.parents:
            if s[m.P_TZ][0] != "commit":
                continue
            commits += 1
            g = list(s[: m.NG])
            g[m.TZ] = m.T2  # 대상은 tz UPDATE 뒤에 읽는다
            adj = m.targets(v, g, lay=sc.lay, newtz=m.T2)
            n = len(g[m.MK])
            if adj != frozenset(range(n)):
                differ += 1
                miss = tuple(m.DATES[d] for d in sorted(set(range(n)) - adj))
                active = tuple(
                    m.DATES[d] for d in range(n) if m.parts_of(sc.lay, m.T2, di=d)
                )
                kind = (
                    "빠진 날짜에 새 tz 활동 있음"
                    if set(miss) & set(active)
                    else "빠진 날짜는 새 tz 활동 없음"
                )
                missing[miss, kind] += 1
                if example is None:
                    example = m.show_state(s)
        print(
            f"{sc.label():28s} 커밋 직전 상태 {commits:>7,}  (a)≠모든 날짜 {differ:>7,}  "
            f"{dict(missing) if missing else ''}"
        )
        if example:
            print(f"    예: {example}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
