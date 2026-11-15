"""7단계 F-4: 일간 행 세션 합의 변경 표시(결정 D1) 상태 모델과 전수 탐색(표준 라이브러리만).

결정 D1: 일간 행은 그날 시작한 완료 세션의 합(C4·이탈 건수·C5)을 가진다. 그래서
  - 세션 완료 handler는 세션 시작일을 표시한다.
  - 구간·라벨 변경 handler는 변경 범위의 날짜와, 그 범위와 겹치는 **완료된** 세션의 시작일을 표시한다.
  - 표시는 모두 계정 advisory 잠금 안에서 쓰고, 겹치는 세션 조회도 잠금 뒤에 한다.
  - 배치의 날짜 읽기에 그날 시작한 완료 세션의 창을 더한다.

바탕: protocol_model_tz3.py의 날짜가 여럿인 구조. 표시(전역 시퀀스 version)·배치(잠금 없는 선점, 선점 뒤
READ COMMITTED 문장별 원천 읽기, 저장 guard = sv가 클 때만, 같은 version일 때만 표시 삭제, 저장·삭제 한
트랜잭션, 저장 때 정의 확인)·조회(REPEATABLE READ 한 스냅샷)는 V5 그대로다. 시간대는 하나다.

모델 규모: 계정 1개, 날짜 2개(d1·d2). 완료 전 세션 하나가 d1 끝 무렵에 시작해 d2로 넘어간다.
  원천 부분 p = d1의 구간, w = d2 안의 그 세션 창에 있는 구간 부분(값 = 라벨 변경 횟수).
  원천 변경은 "세션 완료"(C)와 "d2 구간 라벨 변경"(L) 두 사건이다. 시나리오가 둘 중 0~2개를 고르고, 두
  사건은 서로 다른 요청(프로세스)이라 어떤 순서·끼어들기로든 일어난다. 배치 2개, 조회 1개(날짜는 조회가
  고름), 정의 변경 0회.

계산: f(def, d, src) = (def, d, ((부분, 값), …)).
  d1 = d1 구간 p + 세션 완료 여부 + (완료면) "d1에 시작한 완료 세션 창 안의 구간·확정" w.
  d2 = d2 구간 w.

원자 단계:
  세션 완료 C   C.draw   잠금 + nextval(S3은 잠금 없이 nextval만)
                C.commit 세션 완료 + 시작일 d1 표시 UPSERT(뽑은 version), 커밋(S2는 표시하지 않음)
  라벨 변경 L   L.draw   잠금 + nextval
                L.lookup 변경 범위와 겹치는 완료 세션 조회(잠금 뒤, 한 문장)
                L.commit w +1, d2 표시 + (조회한 완료 세션이 있으면) 시작일 d1 표시, 커밋(S1은 d2만)
  배치 i        B.start(코드 정의) → B.claim(표시 있는 날짜 하나, version 기억) → B.read(부분을 한
                문장씩: d1은 p, 세션 완료 여부, 완료면 w / d2는 w) → B.final(정의 확인, 저장 guard, 같은
                version일 때만 표시 삭제, 커밋) 또는 롤백
  조회          Q(날짜 선택, 표시·저장 행·원천을 REPEATABLE READ 한 스냅샷으로 읽고 계산) 한 단계

불변식(protocol_model_tz3.py와 같은 뜻, 시간대 항목 제외):
  I1  조회 결과 = 조회가 걸친 어느 순간의 원천으로 계산한 값. J: 어느 상태에서든 날짜마다 한 스냅샷 조회
      결과 = 그 상태의 원천 계산값.
  I2  모든 프로세스가 끝난 뒤 배치를 혼자 최대 2번 더 돌리면 표시 0개, 저장 행 = 현재 정의·원천 계산값.
  I3  조회 결과의 정의 = 조회 코드의 정의.
  I5  저장 행의 정의 version은 줄지 않는다.
  I8  I2 상태에서 활동이 있는 날짜는 저장 행이 있다.

실행(저장소 루트에서):
  python -m tools.analytics_protocol_models.session_marks                # 모든 변형, 요약표
  python -m tools.analytics_protocol_models.session_marks S0 S3 --trace  # 일부 변형, 위반 반례 출력
  python -m tools.analytics_protocol_models.session_marks --small        # CI용(이 모델은 전체와 같음)
변형마다 결과가 EXPECTED와 다르면 종료 코드 1로 끝난다.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

type State = tuple[Any, ...]

DATES = ("d1", "d2")
D1, D2 = 0, 1
SOLO_RUNS = 2

# ---------------------------------------------------------------- 변형


@dataclass(frozen=True)
class Variant:
    name: str
    note: str
    # 세션 완료 handler가 세션 시작일을 표시하는가
    done_mark: bool = True
    # 세션 완료 handler가 계정 잠금 안에서 version을 뽑고 표시하는가(False면 잠금 없이 미리 뽑은 version)
    done_lock: bool = True
    # 구간·라벨 변경 handler가 겹치는 완료 세션의 시작일을 표시하는가
    overlap_mark: bool = True


@dataclass(frozen=True)
class Scenario:
    # 일어나는 원천 사건: complete(세션 완료) | label(d2 구간 라벨 변경)
    events: tuple[str, ...]

    def label(self) -> str:
        names = {"complete": "세션 완료", "label": "d2 라벨 변경"}
        return "사건 " + ("·".join(names[e] for e in self.events) or "없음")


SCENARIOS = [
    Scenario(()),
    Scenario(("complete",)),
    Scenario(("label",)),
    Scenario(("complete", "label")),
]

# ---------------------------------------------------------------- 상태
# 전역 g의 순서: src, sess, seq, mk, st, dfn, adv
#   src: 부분 값 튜플 p·w, sess: 세션 완료 여부, mk: 날짜별 None | version,
#   st: 날짜별 None | 튜플 def·sv·val, adv: 계정 잠금 보유자 'C' | 'L' | None
SRC, SESS, SEQ, MK, ST, DEF, ADV = range(7)
NG = 7
P, W = 0, 1
# 프로세스: 세션 완료, 라벨 변경, 배치 1·2, 조회
P_DONE, P_LBL, P_B1, P_B2, P_Q = range(NG, NG + 5)


def f(dfn: int, di: int, *, src: tuple[int, int], sess: bool) -> tuple[Any, ...]:
    if di == D1:
        parts: tuple[Any, ...] = (("p", src[P]), ("sess", sess))
        if sess:
            parts += (("w", src[W]),)
    else:
        parts = (("w", src[W]),)
    return (dfn, DATES[di], parts)


def initial(sc: Scenario) -> State:
    src = (0, 0)
    st = tuple((1, 1, f(1, di, src=src, sess=False)) for di in range(len(DATES)))
    g = (src, False, 2, (None, None), st, 1, None)
    done = ("draw", None) if "complete" in sc.events else ("done", None)
    lbl = ("draw", None, None) if "label" in sc.events else ("done", None, None)
    batch = ("start", None, None, None, ())  # pc, mydef, di, cv, reads
    q = ("idle",)
    return g + (done, lbl, batch, batch, q)


def setp(tup: Any, i: int, *, val: Any) -> tuple[Any, ...]:
    lst = list(tup)
    lst[i] = val
    return tuple(lst)


def answer(g: Any, di: int) -> tuple[Any, ...]:
    return f(g[DEF], di, src=g[SRC], sess=g[SESS])


def atomic_query(g: Any, di: int) -> Any:
    """REPEATABLE READ 한 스냅샷 조회: 표시가 없고 저장 행이 현재 정의면 저장 값, 아니면 계산."""
    mrk, row = g[MK][di], g[ST][di]
    if mrk is None and row is not None and row[0] == g[DEF]:
        return row[2]
    return answer(g, di)


def batch_want(di: int, reads: tuple[Any, ...]) -> Any:
    """배치가 다음 문장에서 읽을 원천. 없으면 None(읽기 끝)."""
    if di == D2:
        return "w" if not reads else None
    order = ["p", "sess"]
    if len(reads) >= len(order) and reads[1][1]:
        order.append("w")
    return order[len(reads)] if len(reads) < len(order) else None


def read_source(g: Any, name: str) -> Any:
    if name == "sess":
        return g[SESS]
    return g[SRC][P if name == "p" else W]


# ---------------------------------------------------------------- 전이


def successors(v: Variant, sc: Scenario, *, s: State) -> list[Any]:
    del sc  # 시나리오는 초기 상태에서만 쓰인다
    out: list[Any] = []
    procs = list(s[NG:])

    def emit(label: str, gl: list[Any], *, newprocs: list[Any], viol: Any = ()) -> None:
        g = tuple(gl)
        found: list[str] = list(viol)
        for di in range(len(DATES)):
            a, b = s[ST][di], g[ST][di]
            if a is not None and b is not None and b[0] < a[0]:
                found.append("I5")
        out.append((label, g + tuple(newprocs), found))

    def copy_globals() -> list[Any]:
        return list(s[:NG])

    def with_proc(i: int, val: Any) -> list[Any]:
        p = list(procs)
        p[i - NG] = val
        return p

    adv_free = s[ADV] is None

    # 세션 완료
    cpc, cv = s[P_DONE]
    if cpc == "draw" and (adv_free or not v.done_lock):
        g = copy_globals()
        ver = g[SEQ]
        g[SEQ] += 1
        if v.done_lock:
            g[ADV] = "C"
        how = "잠금, " if v.done_lock else "잠금 없이 "
        emit(
            f"세션 완료 시작: {how}nextval={ver}",
            g,
            newprocs=with_proc(P_DONE, ("commit", ver)),
        )
    elif cpc == "commit":
        g = copy_globals()
        g[SESS] = True
        if v.done_mark:
            g[MK] = setp(g[MK], D1, val=cv)
        if v.done_lock:
            g[ADV] = None
        what = f"d1 표시={cv}" if v.done_mark else "표시 없음"
        emit(
            f"세션 완료 커밋: 세션 완료, {what}",
            g,
            newprocs=with_proc(P_DONE, ("done", None)),
        )

    # d2 구간 라벨 변경
    lpc, lv, seen_done = s[P_LBL]
    if lpc == "draw" and adv_free:
        g = copy_globals()
        ver = g[SEQ]
        g[SEQ] += 1
        g[ADV] = "L"
        emit(
            f"라벨 변경 시작: 잠금, nextval={ver}",
            g,
            newprocs=with_proc(P_LBL, ("lookup", ver, None)),
        )
    elif lpc == "lookup":
        emit(
            f"라벨 변경: 겹치는 완료 세션 조회 → {'있음' if s[SESS] else '없음'}",
            copy_globals(),
            newprocs=with_proc(P_LBL, ("commit", lv, s[SESS])),
        )
    elif lpc == "commit":
        g = copy_globals()
        g[SRC] = (g[SRC][P], g[SRC][W] + 1)
        g[MK] = setp(g[MK], D2, val=lv)
        dates = ["d2"]
        if v.overlap_mark and seen_done:
            g[MK] = setp(g[MK], D1, val=lv)
            dates.append("d1")
        g[ADV] = None
        emit(
            f"라벨 변경 커밋: w +1, 표시 {dates}={lv}",
            g,
            newprocs=with_proc(P_LBL, ("done", None, None)),
        )

    # 배치
    for idx in (P_B1, P_B2):
        batch_successors(s, idx=idx, emit=emit, with_proc=with_proc)

    # 조회(한 스냅샷 한 단계)
    if s[P_Q][0] == "idle":
        for d in range(len(DATES)):
            res = atomic_query(s, d)
            viol = []
            if res[0] != s[DEF]:
                viol.append("I3")
            elif res != answer(s, d):
                viol.append("I1")
            emit(
                f"조회({DATES[d]}) 한 스냅샷: 표시={s[MK][d]}, 저장={s[ST][d]} → 결과 {res}",
                copy_globals(),
                newprocs=with_proc(P_Q, ("done",)),
                viol=viol,
            )
    return out


def batch_successors(s: State, *, idx: int, emit: Any, with_proc: Any) -> None:
    name = f"배치{idx - P_B1 + 1}"
    bpc, mydef, di, cv, reads = s[idx]

    def put(local: Any, g: list[Any], *, label: str) -> None:
        emit(f"{name} {label}", g, newprocs=with_proc(idx, local))

    if bpc == "start":
        put(
            ("claim", s[DEF], None, None, ()),
            list(s[:NG]),
            label=f"시작: 코드 정의={s[DEF]}",
        )
    elif bpc == "claim":
        marked = [d for d in range(len(DATES)) if s[MK][d] is not None]
        for d in marked:
            put(
                ("read", mydef, d, s[MK][d], ()),
                list(s[:NG]),
                label=f"선점: {DATES[d]} 표시={s[MK][d]}",
            )
        if not marked:
            put(("done", None, None, None, ()), list(s[:NG]), label="선점: 일 없음")
    elif bpc == "read":
        want = batch_want(di, reads)
        got = read_source(s, want)
        nreads = (*reads, (want, got))
        nxt = "read" if batch_want(di, nreads) is not None else "final"
        put(
            (nxt, mydef, di, cv, nreads),
            list(s[:NG]),
            label=f"원천 읽기({DATES[di]}) {want}={got}",
        )
    elif bpc == "final":
        put(("done", None, None, None, ()), list(s[:NG]), label="실패: 롤백")
        if s[DEF] != mydef:
            return  # 정의 확인 실패 롤백은 위 롤백과 같다
        g = list(s[:NG])
        val = (mydef, DATES[di], reads)
        row = g[ST][di]
        if row is None or cv > row[1]:
            g[ST] = setp(g[ST], di, val=(mydef, cv, val))
        if g[MK][di] == cv:
            g[MK] = setp(g[MK], di, val=None)
        put(
            ("done", None, None, None, ()),
            g,
            label=f"저장+표시 삭제 커밋: {DATES[di]} 계산값={val}, sv={cv} → "
            f"저장={g[ST][di]}, 표시={g[MK][di]}",
        )


def check_state(s: State) -> list[str]:
    for di in range(len(DATES)):
        if atomic_query(s, di) != answer(s, di):
            return ["I1"]  # J
    return []


def is_terminal(s: State) -> bool:
    return all(p[0] == "done" for p in s[NG:])


def solo_batch(state: State) -> tuple[State, bool]:
    """변경이 멈춘 뒤 혼자 도는 배치 1회: 표시 있는 날짜를 모두 현재 정의·원천으로 처리."""
    g = list(state)
    worked = False
    for di in range(len(DATES)):
        cv = g[MK][di]
        if cv is None:
            continue
        worked = True
        row = g[ST][di]
        if row is None or cv > row[1]:
            g[ST] = setp(g[ST], di, val=(g[DEF], cv, answer(g, di)))
        g[MK] = setp(g[MK], di, val=None)
    return tuple(g), worked


def rows_ok(g: State) -> bool:
    if any(m is not None for m in g[MK]):
        return False
    return all(
        row is None or (row[0] == g[DEF] and row[2] == answer(g, di))
        for di, row in enumerate(g[ST])
    )


def check_terminal(s: State) -> tuple[list[str], int | None]:
    g = s[:NG]
    for runs in range(SOLO_RUNS + 1):
        if rows_ok(g):
            return ["I8" for row in g[ST] if row is None], runs
        if runs == SOLO_RUNS:
            break
        g, worked = solo_batch(g)
        if not worked:
            break
    return ["I2"], None


# ---------------------------------------------------------------- 탐색


@dataclass
class Result:
    states: int = 0
    transitions: int = 0
    terminals: int = 0
    orders: int = 0
    max_depth: int = 0
    solo_runs_max: int = 0
    violations: dict[str, int] = field(default_factory=dict)
    first: dict[str, tuple[Any, Any, Any]] = field(default_factory=dict)
    parents: dict[State, Any] | None = None
    seconds: float = 0.0


def explore(v: Variant, sc: Scenario, *, keep_parents: bool = False) -> Result:
    t0 = time.perf_counter()
    init = initial(sc)
    seen: dict[State, Any] = {init: None}
    depth = {init: 0}
    frontier = deque([init])
    res = Result()

    def hit(kind: str, state: State, *, via: Any = None, prev: Any = None) -> None:
        res.violations[kind] = res.violations.get(kind, 0) + 1
        if kind not in res.first:
            res.first[kind] = (state, via, prev)

    for kind in check_state(init):
        hit(kind, init)
    while frontier:
        s = frontier.popleft()
        succ = successors(v, sc, s=s)
        if not succ:
            if is_terminal(s):
                res.terminals += 1
                viol, runs = check_terminal(s)
                for kind in viol:
                    hit(kind, s)
                if runs is not None:
                    res.solo_runs_max = max(res.solo_runs_max, runs)
            else:
                hit("DEADLOCK", s)
            continue
        for label, ns, viol in succ:
            res.transitions += 1
            for kind in viol:
                hit(kind, ns, via=label, prev=s)
            if ns not in seen:
                seen[ns] = (s, label) if keep_parents else None
                depth[ns] = depth[s] + 1
                res.max_depth = max(res.max_depth, depth[ns])
                for kind in check_state(ns):
                    hit(kind, ns)
                frontier.append(ns)
    res.states = len(seen)
    res.parents = seen if keep_parents else None
    # 실행 순서 수(시작 → 끝 경로 수). 모든 전이가 어떤 프로세스를 한 단계 진행시키므로 순환이 없다.
    res.orders = count_orders(v, sc, init=init)
    res.seconds = time.perf_counter() - t0
    return res


def count_orders(v: Variant, sc: Scenario, *, init: State) -> int:
    memo: dict[State, int] = {}
    onstack: set[State] = set()
    stack: list[tuple[State, list[State] | None]] = [(init, None)]
    while stack:
        st, succ = stack.pop()
        if st in memo:
            continue
        if succ is None:
            succ = [ns for _, ns, _ in successors(v, sc, s=st)]
            onstack.add(st)
            stack.append((st, succ))
            for ns in succ:
                if ns in onstack:
                    msg = "상태 그래프에 순환이 있다"
                    raise RuntimeError(msg)
                if ns not in memo:
                    stack.append((ns, None))
        else:
            memo[st] = 1 if not succ else sum(memo[ns] for ns in succ)
            onstack.discard(st)
    return memo[init]


def trace(res: Result, state: State) -> list[tuple[str, State]]:
    parents = res.parents
    assert parents is not None
    path = []
    while parents[state] is not None:
        prev, label = parents[state]
        path.append((label, state))
        state = prev
    path.reverse()
    return path


def show_state(s: State) -> str:
    rows = "; ".join(
        f"{DATES[di]} 표시={s[MK][di]} 저장={s[ST][di]}" for di in range(len(DATES))
    )
    lock = f" 잠금={s[ADV]}" if s[ADV] is not None else ""
    return (
        f"원천(p={s[SRC][P]}, w={s[SRC][W]}) 세션={'완료' if s[SESS] else '진행'}"
        f"{lock} | {rows}"
    )


# ---------------------------------------------------------------- 변형

S0 = Variant(
    "S0",
    "추천(D1): 세션 완료가 시작일 표시, 구간·라벨 변경이 변경 날짜와 겹치는 완료 세션의 시작일 표시, "
    "모든 표시는 계정 잠금 안, 겹치는 세션 조회는 잠금 뒤",
)
VARIANTS = [
    S0,
    replace(
        S0,
        name="S1",
        note="대조: 구간·라벨 변경이 겹치는 세션의 시작일을 표시하지 않음(ADR 0016 이전 규칙)",
        overlap_mark=False,
    ),
    replace(S0, name="S2", note="대조: 세션 완료가 표시하지 않음", done_mark=False),
    replace(
        S0,
        name="S3",
        note="대조: 세션 완료 handler가 잠금 없이 미리 뽑은 version으로 표시",
        done_lock=False,
    ),
]

# 변형별 기대 결과: 위반한 불변식 집합(빈 집합 = 위반 0). Issue #280: S0 위반 0, S1은 I1 위반,
# S2·S3은 I1 또는 I2 위반. 대조 변형의 집합은 이 모델의 전체 탐색 결과다.
EXPECTED: dict[str, frozenset[str]] = {
    "S0": frozenset(),
    "S1": frozenset({"I1", "I2"}),
    "S2": frozenset({"I1", "I2"}),
    "S3": frozenset({"I1", "I2"}),
}

# 이 모델은 전체 탐색도 작아 CI에서 모든 변형·시나리오를 그대로 돌린다. 그래서 작은 시나리오의
# 기대 결과는 전체 탐색의 기대 결과와 같다.
SMALL_EXPECTED: dict[str, frozenset[str]] = EXPECTED
SMALL_SCENARIOS = SCENARIOS


def verdict(kinds: frozenset[str]) -> str:
    return "통과" if not kinds else "실패 " + ",".join(sorted(kinds))


def print_counterexamples(rows: list[tuple[Scenario, Result]]) -> None:
    best: dict[str, Any] = {}
    for sc, r in rows:
        for kind, (st, via, prev) in r.first.items():
            path = [*trace(r, prev), (via, st)] if prev is not None else trace(r, st)
            if kind not in best or len(path) < len(best[kind][1]):
                best[kind] = (sc, path, st, via)
    for kind, (sc, path, st, via) in sorted(best.items()):
        print(f"  -- {kind} 가장 짧은 반례 ({sc.label()}, {len(path)}단계)")
        print(f"     0. 초기: {show_state(initial(sc))}")
        for i, (label, s2) in enumerate(path, 1):
            print(f"     {i}. {label}\n        → {show_state(s2)}")
        dates = range(len(DATES))
        if kind in ("I2", "I8"):
            g = st[:NG]
            for n in range(SOLO_RUNS):
                g2, worked = solo_batch(g)
                print(
                    f"     +{n + 1}. 추가 배치(혼자): {'처리' if worked else '일 없음'} → "
                    f"{show_state(g2 + (None,) * 5)}"
                )
                g = g2
            print(
                "     정답: " + "; ".join(f"{DATES[d]}={answer(st, d)}" for d in dates)
            )
        if kind == "I1" and via is None:
            print(
                "     이 상태의 한 스냅샷 조회: "
                + "; ".join(
                    f"{DATES[d]} {atomic_query(st, d)} (정답 {answer(st, d)})"
                    for d in dates
                )
            )


def run(names: list[str], *, show_trace: bool, small: bool) -> int:
    default = list(SMALL_EXPECTED) if small else [v.name for v in VARIANTS]
    chosen = [v for v in VARIANTS if v.name in (names or default)]
    scenarios = SMALL_SCENARIOS if small else SCENARIOS
    total_states = total_orders = 0
    summary = []
    t_all = time.perf_counter()
    for v in chosen:
        agg: dict[str, int] = {}
        rows = []
        for sc in scenarios:
            r = explore(v, sc, keep_parents=show_trace)
            total_states += r.states
            total_orders += r.orders
            rows.append((sc, r))
            for k, n in r.violations.items():
                agg[k] = agg.get(k, 0) + n
        kinds = frozenset(agg)
        summary.append(
            (v, kinds, sum(r.states for _, r in rows), sum(r.orders for _, r in rows))
        )
        print(f"\n=== {v.name}: {v.note}", flush=True)
        for sc, r in rows:
            vio = (
                ", ".join(f"{k}:{n}" for k, n in sorted(r.violations.items())) or "없음"
            )
            print(
                f"  {sc.label():28s} 상태 {r.states:>7,} 전이 {r.transitions:>8,} "
                f"끝상태 {r.terminals:>5,} 실행순서 {r.orders:>14,} 깊이 {r.max_depth:>2} "
                f"추가배치≤{r.solo_runs_max} {r.seconds:5.1f}s 위반 {vio}",
                flush=True,
            )
        print(f"  판정: {verdict(kinds)}")
        if show_trace and agg:
            print_counterexamples(rows)
    print("\n=== 요약")
    mismatched = []
    unchecked: list[str] = []
    for v, kinds, ns, no in summary:
        expected = (SMALL_EXPECTED if small else EXPECTED).get(v.name)
        mark = ""
        if expected is None:
            mark = " (이 시나리오의 기대 결과 없음)"
            unchecked.append(v.name)
        elif kinds != expected:
            mark = f" (기대와 다름: {verdict(expected)})"
            mismatched.append(v.name)
        print(
            f"  {v.name:3s} {verdict(kinds):16s} 상태 {ns:>8,} 실행순서 {no:>16,}  {v.note}{mark}"
        )
    print(
        f"\n합계: 상태 {total_states:,}, 실행 순서 {total_orders:,}, "
        f"{time.perf_counter() - t_all:.1f}s"
    )
    if mismatched:
        print(f"기대 결과와 다른 변형: {', '.join(mismatched)}")
        return 1
    if unchecked:
        print(f"기대 결과가 없어 비교하지 않은 변형: {', '.join(unchecked)}")
    print("비교한 모든 변형이 기대 결과와 같다")
    return 0


def main(argv: list[str]) -> int:
    names = [a for a in argv if not a.startswith("--")]
    return run(names, show_trace="--trace" in argv, small="--small" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
