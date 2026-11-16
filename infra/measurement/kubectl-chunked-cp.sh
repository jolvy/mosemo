#!/usr/bin/env bash
# kubectl 대리: 'cp'만 2MB 조각 전송(exec dd)으로 바꾸고 나머지는 진짜 kubectl로 넘긴다.
REAL="/opt/homebrew/bin/kubectl"
args=("$@"); ns=""; i=0; rest=()
while (( i < ${#args[@]} )); do
  a=${args[$i]}
  if [[ $a == -n ]]; then ns=${args[$((i+1))]}; i=$((i+2)); continue; fi
  rest+=("$a"); i=$((i+1))
done
[[ ${rest[0]} == cp ]] || exec "$REAL" "$@"
src=${rest[1]}; dst=${rest[2]}; ctr=""
for ((j=3; j<${#rest[@]}; j++)); do [[ ${rest[$j]} == -c ]] && ctr=${rest[$((j+1))]}; done
CH=2097152
remote() { local s=$1; s=${s#*/}; pod=${s%%:*}; path=${s#*:}; }
if [[ $dst == *:* && $src != *:* && ( -d "$src" || $(stat -f %z "$src") -lt 8388608 ) ]]; then exec "$REAL" "$@"   # 폴더·8MB 미만은 그대로
elif [[ $dst == *:* && $src != *:* ]]; then   # 업로드
  remote "$dst"; sz=$(stat -f %z "$src"); n=$(( (sz + CH - 1) / CH ))
  "$REAL" -n "$ns" exec "$pod" -c "$ctr" -- bash -c "rm -rf '$path.parts' && mkdir -p '$path.parts'" || exit 1
  for ((k=0; k<n; k++)); do
    exp=$(( k == n-1 ? sz - k*CH : CH )); ok=0
    for t in 1 2 3; do
      got=$(dd if="$src" bs=$CH skip=$k count=1 2>/dev/null | "$REAL" -n "$ns" exec -i "$pod" -c "$ctr" -- sh -c "cat > '$path.parts/$(printf %05d $k)'; stat -c %s '$path.parts/$(printf %05d $k)'")
      [[ "$got" == "$exp" ]] && { ok=1; break; }
    done
    (( ok )) || { echo "kshim: upload chunk $k failed" >&2; exit 1; }
  done
  "$REAL" -n "$ns" exec "$pod" -c "$ctr" -- bash -c "cat '$path.parts'/* > '$path.tmp' && mv '$path.tmp' '$path' && rm -rf '$path.parts'"
elif [[ $src == *:* && $dst != *:* ]]; then  # 다운로드
  remote "$src"; sz=$("$REAL" -n "$ns" exec "$pod" -c "$ctr" -- stat -c %s "$path") || exit 1
  n=$(( (sz + CH - 1) / CH )); : > "$dst"
  for ((k=0; k<n; k++)); do
    exp=$(( k == n-1 ? sz - k*CH : CH )); ok=0
    for t in 1 2 3; do
      T=$(mktemp); "$REAL" -n "$ns" exec "$pod" -c "$ctr" -- dd if="$path" bs=$CH skip=$k count=1 status=none > "$T"
      [[ $(stat -f %z "$T") == $exp ]] && { cat "$T" >> "$dst"; rm -f "$T"; ok=1; break; }; rm -f "$T"
    done
    (( ok )) || { echo "kshim: download chunk $k failed" >&2; exit 1; }
  done
else exec "$REAL" "$@"; fi
