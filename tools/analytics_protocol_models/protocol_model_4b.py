"""4B 후속 F-1: V5 + 재시도·lease·격리 열의 상태 모델과 전수 탐색(표준 라이브러리만).

protocol_model.py(4단계 D)는 고치지 않는다. 이 파일은 추천 변형 V5만 따로 옮겨 적고 STEP4B-FINAL.md
1.4·1.5·2절과 9.2절 F-1의 전이를 더한다. V5의 고정 선택: 시퀀스 version 표시, 같은 version일 때만 삭제,
guard = sv가 클 때만, 선점 뒤 READ COMMITTED 두 문장 읽기, 저장·삭제 한 트랜잭션, 재처리 mark(계정
advisory 잠금 안), 저장 때 정의 확인, 조회 = 표시+저장 한 문장 + 원천 한 스냅샷.

표시 행: None | (version, attempts, nat)
  nat(next_attempt_at의 추상화): 'r' = 지금 선점 가능, 'l' = lease 중(선점이 now()+10분을 씀),
  'b' = backoff 중(실패 기록이 now()+backoff를 씀). 시간은 값 없이 "lease 끝남"·"backoff 끝남" 전이로 둔다.
격리 문턱 K = 2(9.2절대로 6을 2로 줄임). attempts >= K면 선점 대상에서 빠진다.

원자 단계(4단계 D 단계에 더하거나 바꾼 것):
  B.claim   짧은 트랜잭션 하나: 표시가 있고 attempts < K이고 nat = 'r'이면 attempts+1, nat = 'l',
            version v를 기억, 커밋
  B.final   저장 트랜잭션(정의 FOR SHARE 확인 → stats UPSERT guard → DELETE … version = v) 커밋
            정의가 다르면 롤백하고 실행을 끝낸다(분류 D, 기록 없음, lease 남음)
  B.fail    계산 예외·날짜 단위 DB 오류(분류 A·B): 저장 트랜잭션 없이(또는 롤백) → B.failrec
  B.failrec 실패 기록 트랜잭션: UPDATE 표시 SET nat = 'b' WHERE version = v, 커밋
  B.crash   강제 종료(분류 C·E·F): 열린 트랜잭션은 롤백, 기록 없음, lease 남음(선점 뒤 어느 단계에서나)
  L.expire  lease 끝남: nat 'l' → 'r'          T.backoff  backoff 끝남: nat 'b' → 'r'
  R.release 격리 해제(runbook SQL): attempts >= K이면 attempts = 0, nat = 'r'(version 그대로).
            운영자 1회, 안 할 수도 있음
  S.commit / D.commit  표시 UPSERT가 새 version과 함께 attempts = 0, nat = 'r'로 되돌린다(2.2·2.3절)
예산: 실패(B.fail) 전체 1회, 강제 종료(B.crash) 전체 1회. 그 밖의 범위는 사전 등록 D와 같다.

불변식: I1(J 포함)·I2·I3·I4·I5는 protocol_model.py와 같다. 바꾼 것과 새 것:
  I2  끝 상태에서 격리된 표시가 있으면 먼저 해제하고, 새 배치를 혼자 최대 2회 더 돌리면 수렴한다.
  I6  격리된 표시(attempts >= K)는 저장 없이 사라지지 않는다: 표시가 사라지는 전이 뒤에는 저장 행의
      sv >= 사라진 version이다. 격리되지 않은 표시에도 같은 규칙을 검사해 I6g로 따로 센다.
  I7  새 version은 이전 version의 시도 횟수를 물려받지 않는다: 늘 attempts <= (현재 version을 읽은
      선점 수, 유령 변수).

실행(저장소 루트에서):
  python -m tools.analytics_protocol_models.protocol_model_4b                # 모든 변형
  python -m tools.analytics_protocol_models.protocol_model_4b F5 N2 --trace  # 일부, 위반 반례 출력
  python -m tools.analytics_protocol_models.protocol_model_4b --fails 2 --crashes 2   # 예산 바꾸기
  python -m tools.analytics_protocol_models.protocol_model_4b --small        # CI용 작은 시나리오
변형마다 결과가 EXPECTED와 다르면 종료 코드 1로 끝난다(기대 결과는 기본 예산 실패 1·강제 종료 1의 값).
"""

from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

type State = tuple[Any, ...]

K = 2
SOLO_RUNS = 2

# ---------------------------------------------------------------- 변형


@dataclass(frozen=True)
class Variant:
    name: str
    note: str
    # col: 선점이 nat='l'을 커밋 | none: lease를 쓰지 않음(nat 그대로)
    # | lock: 계산 내내 FOR UPDATE 유지
    lease: str = "col"
    # eq: 실패 기록 WHERE version = v | any: version 조건 없음
    # | delete: 실패하면 표시 삭제(version = v)
    fail_write: str = "eq"
    # 새 version UPSERT가 attempts를 0으로 되돌리는가
    reset: bool = True


F5 = Variant(
    "F5",
    "추천: V5 + lease 열·선점 때 attempts+1·실패 기록 WHERE version=v·새 version에서 attempts 0",
)
VARIANTS = [
    F5,
    replace(
        F5,
        name="N1",
        note="대조: lease 없이 선점(attempts+1만, next_attempt_at 그대로)",
        lease="none",
    ),
    replace(
        F5,
        name="N2",
        note="대조: 실패하면 표시를 삭제(WHERE version=v)",
        fail_write="delete",
    ),
    replace(
        F5,
        name="N3",
        note="대조: 원천 변경·mark UPSERT가 attempts를 유지",
        reset=False,
    ),
    replace(
        F5,
        name="N4",
        note="대조(9.2절): 실패 기록에 version 조건 없음",
        fail_write="any",
    ),
    replace(
        F5,
        name="N5",
        note="대조(9.2절): lease 대신 FOR UPDATE를 계산 내내 유지(V6형)",
        lease="lock",
    ),
]


@dataclass(frozen=True)
class Scenario:
    def_change: bool
    init: str
    parts: tuple[str, ...]

    def label(self) -> str:
        return f"정의변경{'1' if self.def_change else '0'}·{self.init}·{'→'.join(self.parts)}"


SCENARIOS = [
    Scenario(d, i, p)
    for d in (False, True)
    for i in ("stored", "marker")
    for p in (("a", "b"), ("b", "a"))
]

# ---------------------------------------------------------------- 상태
# 전역 g의 순서: a, b, seq, marker, mlock, stats, cur_def, adv, gc, fails, crashes
#   marker: None | 튜플 ver·att·nat    stats: None | 튜플 def·sv·val
#   gc: 현재 version을 읽은 선점 수(유령)
# 프로세스: src, dfn, b1, b2, q, rel
A, B_, SEQ, MARK, MLOCK, STATS, DEF, ADV, GC, FAILS, CRASH = range(11)
NG = 11
SRC, DFN, B1, B2, Q, REL = range(NG, NG + 6)


def f(d: int, a: int, *, b: int) -> tuple[int, int, int]:
    return (d, a, b)


def initial(sc: Scenario, *, fails: int, crashes: int) -> State:
    if sc.init == "stored":
        stats, marker = (1, 1, f(1, 0, b=0)), None
    else:
        stats, marker = None, (1, 0, "r")
    g = (0, 0, 2, marker, None, stats, 1, None, 0, fails, crashes)
    src = ("draw", 0, None)
    dfn = ("bump", None) if sc.def_change else ("done", None)
    batch = ("start", None, None, None, None)  # pc, mydef, v, ra, rb
    q = ("idle", None, None, None, frozenset())  # pc, qdef, ra, res, valid
    rel = "wait"
    return g + (src, dfn, batch, batch, q, rel)


def atomic_query(g: State) -> Any:
    a, b, marker, stats, cur = g[A], g[B_], g[MARK], g[STATS], g[DEF]
    if marker is not None or stats is None or stats[0] != cur:
        return f(cur, a, b=b)
    return stats[2]


def new_version_marker(v: Variant, old: Any, *, ver: int) -> tuple[int, int, str]:
    """원천 handler·mark의 UPSERT: 새 version. 설계는 attempts=0, next_attempt_at=now()."""
    att = 0 if (v.reset or old is None) else old[1]
    return (ver, att, "r")


# ---------------------------------------------------------------- 전이


def successors(v: Variant, sc: Scenario, *, s: State) -> list[Any]:
    out = []
    procs = list(s[NG:])
    src, dfn, _b1, _b2, q, rel = procs

    def emit(label: str, g: list[Any], *, p: list[Any], viol: Any = ()) -> None:
        gt = tuple(g)
        ns = gt + tuple(p)
        qq = ns[Q]
        if qq[0] not in ("idle", "done"):
            ns = (
                ns[:Q]
                + ((qq[0], qq[1], qq[2], qq[3], qq[4] | {f(qq[1], gt[A], b=gt[B_])}),)
                + ns[Q + 1 :]
            )
        found: list[str] = list(viol)
        if (
            s[STATS] is not None
            and gt[STATS] is not None
            and gt[STATS][0] < s[STATS][0]
        ):
            found.append("I5")
        old = s[MARK]
        if (
            old is not None
            and gt[MARK] is None
            and (gt[STATS] is None or gt[STATS][1] < old[0])
        ):
            found.append("I6" if old[1] >= K else "I6g")
        out.append((label, ns, found))

    def setp(i: int, val: Any) -> list[Any]:
        p = list(procs)
        p[i - NG] = val
        return p

    # 원천 변경(계정 advisory 잠금으로 직렬)
    pc, k, drawn = src
    if pc == "draw" and s[ADV] is None:
        g = list(s[:NG])
        val = g[SEQ]
        g[SEQ] += 1
        g[ADV] = "S"
        emit(
            f"원천{k + 1} 시작: advisory 잠금, nextval={val}",
            g,
            p=setp(SRC, ("commit", k, val)),
        )
    elif pc == "commit" and s[MLOCK] is None:
        g = list(s[:NG])
        part = sc.parts[k]
        g[A if part == "a" else B_] += 1
        g[MARK] = new_version_marker(v, s[MARK], ver=drawn)
        g[GC] = 0
        g[ADV] = None
        nxt = ("draw", k + 1, None) if k + 1 < len(sc.parts) else ("done", k + 1, None)
        emit(f"원천{k + 1} 커밋: {part} +1, 표시={g[MARK]}", g, p=setp(SRC, nxt))

    # 정의 변경 + mark
    dpc, ddrawn = dfn
    if dpc == "bump":
        g = list(s[:NG])
        g[DEF] = 2
        emit("정의 version 1→2", g, p=setp(DFN, ("draw", None)))
    elif dpc == "draw" and s[ADV] is None:
        g = list(s[:NG])
        val = g[SEQ]
        g[SEQ] += 1
        g[ADV] = "D"
        emit(
            f"mark 시작: advisory 잠금, nextval={val}", g, p=setp(DFN, ("commit", val))
        )
    elif dpc == "commit" and s[MLOCK] is None:
        g = list(s[:NG])
        g[MARK] = new_version_marker(v, s[MARK], ver=ddrawn)
        g[GC] = 0
        g[ADV] = None
        emit(f"mark 커밋: 표시={g[MARK]}", g, p=setp(DFN, ("done", None)))

    # 시간 경과(환경)
    m = s[MARK]
    if m is not None and m[2] == "l":
        g = list(s[:NG])
        g[MARK] = (m[0], m[1], "r")
        emit(f"lease 끝남: 표시={g[MARK]}", g, p=procs)
    if m is not None and m[2] == "b":
        g = list(s[:NG])
        g[MARK] = (m[0], m[1], "r")
        emit(f"backoff 끝남: 표시={g[MARK]}", g, p=procs)

    # 격리 해제(운영자, 최대 1회, 안 할 수도 있음)
    if rel == "wait":
        if m is not None and m[1] >= K:
            g = list(s[:NG])
            g[MARK] = (m[0], 0, "r")
            emit(f"격리 해제: 표시={g[MARK]}", g, p=setp(REL, "done"))
        emit("운영자: 해제하지 않음", list(s[:NG]), p=setp(REL, "done"))

    # 배치
    for idx in (B1, B2):
        batch_successors(v, s=s, idx=idx, emit=emit, setp=setp)

    # 조회
    qpc, qdef, _qa, _qres, valid = q

    def finish(res: Any, label: str) -> None:
        here = valid | {f(qdef, s[A], b=s[B_])}
        viol = []
        if res[0] != qdef:
            viol.append("I3")
        elif res not in here:
            viol.append("I1")
        emit(
            f"조회 {label} → 결과 {res}",
            list(s[:NG]),
            p=setp(Q, ("done", qdef, None, res, here)),
            viol=viol,
        )

    if qpc == "idle":
        start_valid = frozenset({f(s[DEF], s[A], b=s[B_])})
        st = s[STATS]
        if m is None and st is not None and st[0] == s[DEF]:
            emit(
                f"조회 표시+저장 한 문장: 저장={st} → 결과 {st[2]}",
                list(s[:NG]),
                p=setp(Q, ("done", s[DEF], None, st[2], start_valid)),
            )
        else:
            emit(
                f"조회 표시+저장 한 문장: 표시={m}, 저장={st} → 원천 계산",
                list(s[:NG]),
                p=setp(Q, ("ca", s[DEF], None, None, start_valid)),
            )
    elif qpc == "ca":
        finish(f(qdef, s[A], b=s[B_]), f"원천 읽기(한 스냅샷): a={s[A]}, b={s[B_]}")
    return out


def batch_successors(v: Variant, *, s: State, idx: int, emit: Any, setp: Any) -> None:
    """배치 idx의 다음 전이를 emit으로 내보낸다(원본 successors의 배치 반복 한 번)."""
    bi = idx - B1 + 1
    m = s[MARK]
    bpc, mydef, cv, ra, rb = s[idx]
    name = f"배치{bi}"

    def put(local: Any, g: list[Any], *, label: str, viol: Any = ()) -> None:
        emit(f"{name} {label}", g, p=setp(idx, local), viol=viol)

    def release_lock(g: list[Any]) -> None:
        if g[MLOCK] == bi:
            g[MLOCK] = None

    if bpc == "start":
        put(
            ("claim", s[DEF], None, None, None),
            list(s[:NG]),
            label=f"시작: 코드 정의={s[DEF]}",
        )
    elif bpc == "claim":
        g = list(s[:NG])
        claimable = (
            m is not None
            and m[1] < K
            and (
                m[2] == "r" if v.lease != "lock" else (m[2] == "r" and s[MLOCK] is None)
            )
        )
        if claimable:
            if v.lease == "col":
                g[MARK] = (m[0], m[1] + 1, "l")
            elif v.lease == "none":
                g[MARK] = (m[0], m[1] + 1, m[2])
            else:
                g[MARK] = (m[0], m[1] + 1, m[2])
                g[MLOCK] = bi
            g[GC] += 1
            put(("ra", mydef, m[0], None, None), g, label=f"선점 커밋: 표시={g[MARK]}")
        else:
            why = (
                "없음"
                if m is None
                else (
                    "격리"
                    if m[1] >= K
                    else ("lease/backoff" if m[2] != "r" else "잠김(SKIP LOCKED)")
                )
            )
            put(("done", mydef, None, None, None), g, label=f"선점: 일 없음({why})")
    if bpc in ("ra", "rb", "final", "failrec") and s[CRASH] > 0:
        g = list(s[:NG])
        g[CRASH] -= 1
        release_lock(g)  # 연결 종료 → 잠금 해제, 열린 트랜잭션 롤백
        put(
            ("done", mydef, None, None, None),
            g,
            label="강제 종료(기록 없음, lease 남음)",
        )
    if bpc == "ra":
        put(("rb", mydef, cv, s[A], None), list(s[:NG]), label=f"원천 읽기 1: a={s[A]}")
    elif bpc == "rb":
        put(
            ("final", mydef, cv, ra, s[B_]),
            list(s[:NG]),
            label=f"원천 읽기 2: b={s[B_]}",
        )
    elif bpc == "final":
        if s[FAILS] > 0:
            g = list(s[:NG])
            g[FAILS] -= 1
            put(("failrec", mydef, cv, None, None), g, label="계산·저장 실패(롤백)")
        if s[DEF] != mydef:
            g = list(s[:NG])
            release_lock(g)
            put(
                ("done", mydef, None, None, None),
                g,
                label="저장 ① 정의 불일치 → 롤백, 실행 종료(기록 없음)",
            )
        else:
            g = list(s[:NG])
            val = f(mydef, ra, b=rb)
            if g[STATS] is None or cv > g[STATS][1]:
                g[STATS] = (mydef, cv, val)
            if g[MARK] is not None and g[MARK][0] == cv:
                g[MARK] = None
            release_lock(g)
            put(
                ("done", mydef, None, None, None),
                g,
                label=f"저장+표시 삭제 커밋: 계산값={val}, sv={cv} → 저장={g[STATS]}, "
                f"표시={g[MARK]}",
            )
    elif bpc == "failrec":
        g = list(s[:NG])
        mm = g[MARK]
        if v.fail_write == "delete":
            if mm is not None and mm[0] == cv:
                g[MARK] = None
        elif mm is not None and (v.fail_write == "any" or mm[0] == cv):
            g[MARK] = (mm[0], mm[1], "b")
        release_lock(g)
        put(
            ("done", mydef, None, None, None),
            g,
            label=f"실패 기록 커밋: 표시={g[MARK]}",
        )


def check_state(s: State) -> list[str]:
    viol = []
    if atomic_query(s[:NG]) != f(s[DEF], s[A], b=s[B_]):
        viol.append("I1")
    if s[SRC][0] == "commit" and s[MLOCK] is not None:
        viol.append("I4")
    if s[DFN][0] == "commit" and s[MLOCK] is not None:
        viol.append("I4")
    m = s[MARK]
    if m is not None and m[1] > s[GC]:
        viol.append("I7")
    return viol


def is_terminal(s: State) -> bool:
    return (
        all(p[0] == "done" for p in (s[SRC], s[DFN], s[B1], s[B2], s[Q]))
        and s[REL] == "done"
    )


def solo_batch(g: State) -> tuple[State, bool]:
    """변경·장애가 멈춘 뒤 혼자 도는 배치 1회(시간이 지나 lease·backoff는 끝남, 현재 정의 코드)."""
    gl = list(g)
    m = gl[MARK]
    if m is None or m[1] >= K:
        return tuple(gl), False
    cv = m[0]
    val = f(gl[DEF], gl[A], b=gl[B_])
    if gl[STATS] is None or cv > gl[STATS][1]:
        gl[STATS] = (gl[DEF], cv, val)
    gl[MARK] = None
    return tuple(gl), True


def converged(g: State) -> bool:
    return (
        g[MARK] is None
        and g[STATS] is not None
        and g[STATS][0] == g[DEF]
        and g[STATS][2] == f(g[DEF], g[A], b=g[B_])
    )


def check_terminal(s: State) -> tuple[list[str], int | None, bool]:
    gl = list(s[:NG])
    released = False
    if gl[MARK] is not None and gl[MARK][1] >= K:
        gl[MARK] = (gl[MARK][0], 0, "r")
        released = True
    g = tuple(gl)
    for runs in range(SOLO_RUNS + 1):
        if converged(g):
            return [], runs, released
        if runs == SOLO_RUNS:
            break
        g, worked = solo_batch(g)
        if not worked:
            break
    return ["I2"], None, released


# ---------------------------------------------------------------- 탐색


@dataclass
class Result:
    states: int = 0
    transitions: int = 0
    terminals: int = 0
    terminals_quarantined: int = 0
    orders: int = 0
    complete: bool = False
    max_depth: int = 0
    solo_runs_max: int = 0
    quarantine_states: int = 0
    quarantine_deleted: int = 0  # 격리 표시가 (저장과 함께) 사라진 전이 수
    violations: dict[str, int] = field(default_factory=dict)
    first: dict[str, tuple[Any, Any, Any]] = field(default_factory=dict)
    parents: dict[State, Any] | None = None
    seconds: float = 0.0


def explore(
    v: Variant, sc: Scenario, *, fails: int, crashes: int, keep_parents: bool = False
) -> Result:
    t0 = time.perf_counter()
    init = initial(sc, fails=fails, crashes=crashes)
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
        if s[MARK] is not None and s[MARK][1] >= K:
            res.quarantine_states += 1
        succ = successors(v, sc, s=s)
        if not succ:
            if is_terminal(s):
                res.terminals += 1
                viol, runs, released = check_terminal(s)
                if released:
                    res.terminals_quarantined += 1
                for kind in viol:
                    hit(kind, s)
                if runs is not None:
                    res.solo_runs_max = max(res.solo_runs_max, runs)
            else:
                hit("DEADLOCK", s)
            continue
        for label, ns, viol in succ:
            res.transitions += 1
            if s[MARK] is not None and s[MARK][1] >= K and ns[MARK] is None:
                res.quarantine_deleted += 1
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
    res.complete = True
    res.parents = seen if keep_parents else None
    del depth

    # 시작 → 끝 경로 수(후위 순회). 모든 전이는 프로세스 단계를 진행시키거나 nat를 'r'로 되돌리므로
    # 순환이 없다.
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
    lock = f" 잠금=배치{s[MLOCK]}" if s[MLOCK] is not None else ""
    return (
        f"원천(a={s[A]}, b={s[B_]}) 정의={s[DEF]} 표시={s[MARK]}{lock} "
        f"저장={s[STATS]} 선점수(유령)={s[GC]}"
    )


# 변형별 기대 결과(기본 예산 실패 1·강제 종료 1): 위반한 불변식 집합(빈 집합 = 위반 0).
# README 표의 F5 위반 0, N2(I1·I2·I6), N3(I7), N5(I4), N1·N4 통과. 모델은 I6 규칙(표시가 저장 없이
# 사라지지 않음)을 격리되지 않은 표시에서 I6g로 따로 세므로, 기대 결과와 비교할 때는 I6과 함께 나온 I6g를
# I6에 포함된 것으로 본다(comparable). 요약의 판정에는 모델이 센 이름이 그대로 나온다.
EXPECTED: dict[str, frozenset[str]] = {
    "F5": frozenset(),
    "N1": frozenset(),
    "N2": frozenset({"I1", "I2", "I6"}),
    "N3": frozenset({"I7"}),
    "N4": frozenset(),
    "N5": frozenset({"I4"}),
}

# CI용 작은 시나리오: 추천 F5와 대조 N5·N2를 정의 변경 없는 시나리오 하나로. N2는 I6과 함께 나온 I6g를
# I6에 포함된 것으로 보는 비교(comparable)를 확인한다.
# 작은 시나리오의 기대 결과(전체 탐색의 EXPECTED와 따로 둔다). --small은 이 변형만 돌리고 이 값과 비교한다.
SMALL_EXPECTED: dict[str, frozenset[str]] = {
    "F5": frozenset(),
    "N5": frozenset({"I4"}),
    "N2": frozenset({"I1", "I2", "I6"}),
}
SMALL_SCENARIOS = [Scenario(def_change=False, init="stored", parts=("a", "b"))]


def comparable(kinds: frozenset[str]) -> frozenset[str]:
    """기대 결과와 비교할 위반 집합.

    I6g는 I6 규칙을 격리되지 않은 표시에서 따로 센 것이라, 격리 표시의 I6과 함께 나올 때만 I6에 포함된
    것으로 보고 뺀다. I6 없이 I6g만 나오면 그대로 남겨 격리 I6이 사라진 회귀나 새 I6g 위반을 가리지 않는다.
    """
    return kinds - {"I6g"} if "I6" in kinds else kinds


def verdict(kinds: frozenset[str]) -> str:
    return "통과" if not kinds else "실패 " + ",".join(sorted(kinds))


def print_counterexamples(
    rows: list[tuple[Scenario, Result]], *, fails: int, crashes: int
) -> None:
    best: dict[str, Any] = {}
    for sc, r in rows:
        for kind, (st, via, prev) in r.first.items():
            path = [*trace(r, prev), (via, st)] if prev is not None else trace(r, st)
            if kind not in best or len(path) < len(best[kind][1]):
                best[kind] = (sc, path, st)
    for kind, (sc, path, st) in sorted(best.items()):
        print(f"  -- {kind} 가장 짧은 반례 ({sc.label()}, {len(path)}단계)")
        print(f"     0. 초기: {show_state(initial(sc, fails=fails, crashes=crashes))}")
        for i, (label, s2) in enumerate(path, 1):
            print(f"     {i}. {label}\n        → {show_state(s2)}")
        answer = f(st[DEF], st[A], b=st[B_])
        if kind == "I1" and path and atomic_query(st[:NG]) != answer:
            print(
                f"     이 상태의 한 스냅샷 조회 결과 {atomic_query(st[:NG])} ≠ 정답 {answer}"
            )
        if kind == "I2":
            print(
                "     끝 상태에서 (격리면 해제 뒤) 추가 배치 2회로 수렴하지 않음. "
                f"정답 {answer}"
            )


def run(
    names: list[str], *, show_trace: bool, small: bool, fails: int, crashes: int
) -> int:
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
            r = explore(v, sc, fails=fails, crashes=crashes, keep_parents=show_trace)
            total_states += r.states
            total_orders += r.orders
            rows.append((sc, r))
            for k, n in r.violations.items():
                agg[k] = agg.get(k, 0) + n
            if not show_trace:
                r.parents = None
        kinds = frozenset(agg)
        summary.append(
            (
                v,
                kinds,
                sum(r.states for _, r in rows),
                sum(r.orders for _, r in rows),
            )
        )
        print(
            f"\n=== {v.name}: {v.note}  (K={K}, 실패≤{fails}, 강제 종료≤{crashes})",
            flush=True,
        )
        for sc, r in rows:
            vio = (
                ", ".join(f"{k}:{n}" for k, n in sorted(r.violations.items())) or "없음"
            )
            print(
                f"  {sc.label():22s} 상태 {r.states:>9,} 전이 {r.transitions:>10,} "
                f"끝 {r.terminals:>7,} (격리끝 {r.terminals_quarantined:>5,}) "
                f"순서 {r.orders:>22,} 깊이 {r.max_depth:>2} "
                f"완료={'예' if r.complete else '아니오'} 추가배치≤{r.solo_runs_max} "
                f"격리상태 {r.quarantine_states:>7,} "
                f"격리표시삭제전이 {r.quarantine_deleted:>5,} {r.seconds:6.1f}s 위반 {vio}",
                flush=True,
            )
        print(f"  판정: {verdict(kinds)}", flush=True)
        if show_trace and agg:
            print_counterexamples(rows, fails=fails, crashes=crashes)
        for _, r in rows:
            r.parents = None
    print("\n=== 요약")
    budget_default = (fails, crashes) == (1, 1)
    mismatched = []
    unchecked: list[str] = []
    for v, kinds, st, od in summary:
        expected = (SMALL_EXPECTED if small else EXPECTED).get(v.name)
        mark = ""
        if expected is None:
            mark = " (이 시나리오의 기대 결과 없음)"
            unchecked.append(v.name)
        elif budget_default and comparable(kinds) != expected:
            mark = f" (기대와 다름: {verdict(expected)})"
            mismatched.append(v.name)
        print(
            f"  {v.name:3s} {verdict(kinds):28s} 상태 {st:>11,} 순서 {od:>24,}  {v.note}{mark}"
        )
    print(
        f"\n합계: 상태 {total_states:,}, 실행 순서 {total_orders:,}, "
        f"{time.perf_counter() - t_all:.1f}s"
    )
    if not budget_default:
        print("기대 결과는 기본 예산(실패 1·강제 종료 1)에서만 비교한다")
        return 0
    if mismatched:
        print(f"기대 결과와 다른 변형: {', '.join(mismatched)}")
        return 1
    if unchecked:
        print(f"기대 결과가 없어 비교하지 않은 변형: {', '.join(unchecked)}")
    print("비교한 모든 변형이 기대 결과와 같다")
    return 0


def main(argv: list[str]) -> int:
    fails = crashes = 1
    names = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--fails":
            fails = int(argv[i + 1])
            i += 2
            continue
        if a == "--crashes":
            crashes = int(argv[i + 1])
            i += 2
            continue
        if not a.startswith("--"):
            names.append(a)
        i += 1
    return run(
        names,
        show_trace="--trace" in argv,
        small="--small" in argv,
        fails=fails,
        crashes=crashes,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
