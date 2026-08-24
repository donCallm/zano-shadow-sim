#!/usr/bin/env bash
# Разбор прогона стенда Zano: такт, соотношение типов, дрейф меток, отказы,
# распределение блоков по стейкерам.
#
# Использование:  bash analyze_run.sh [каталог shadow.data] [лог zanod]
#
# SKIP=<высота> — граница прогрева. Стартовая сложность PoS далека от
# равновесия, ретаргет тратит на разгон десятки блоков, и средние по всему
# прогону меряют разгон, а не установившийся режим. По умолчанию отбрасывается
# первая половина прогона.
D=${1:-$HOME/zanosim/shadow.data}
L=${2:-/tmp/zpos/n1/zanod.log}
H=$D/hosts/zano1
export SKIP=${SKIP:-auto}

awk '
  /BLOCK SUCCESSFULLY ADDED \[Po/ {
    t = ($0 ~ /\[PoS\]/) ? "PoS" : "PoW"
    split($2, c, ":"); simt = c[1]*3600 + c[2]*60 + c[3]
    sq = (match($0,/Sq: [0-9]+/)) ? substr($0,RSTART+4,RLENGTH-4) : "0"
    bts = ""; next }
  t != "" && match($0,/block ts: [0-9]+/) { bts = substr($0,RSTART+10,RLENGTH-10); next }
  t != "" && match($0,/HEIGHT [0-9]+, difficulty: [0-9]+/) {
    match($0,/HEIGHT [0-9]+/);      h = substr($0,RSTART+7,RLENGTH-7)
    match($0,/difficulty: [0-9]+/); d = substr($0,RSTART+12,RLENGTH-12)
    printf "%s\t%s\t%.3f\t%s\t%s\t%s\n", t, h, simt, bts, d, sq; t = "" }
' "$L" | sort -t$'\t' -k2,2n > /tmp/blocks.tsv

python3 - <<'PY'
import os, statistics as st
R=[l.split('\t') for l in open('/tmp/blocks.tsv').read().splitlines()]
R=[(t,int(h),float(s),int(b),int(d),int(q)) for t,h,s,b,d,q in R]
t0,b0=R[0][2],R[0][3]
skip=os.environ.get("SKIP","auto")
cut = R[len(R)//2][1] if skip=="auto" else int(skip)

def block(rows, title):
    if len(rows) < 10:
        print(f"\n### {title}: блоков {len(rows)} — мало для выводов"); return
    iv=[y[2]-x[2] for x,y in zip(rows,rows[1:])]
    ps=sum(1 for r in rows if r[0]=='PoS'); pw=len(rows)-ps
    print(f"\n### {title}: {len(rows)} блоков, высоты {rows[0][1]}..{rows[-1][1]}, "
          f"{(rows[-1][2]-rows[0][2])/3600:.2f} ч")
    print(f"  СУММАРНЫЙ ТАКТ: среднее {st.mean(iv):6.1f} с, медиана {st.median(iv):6.1f} с   (цель 60)")
    print(f"  PoS : PoW = {ps} : {pw} = {ps/max(1,pw):.2f} : 1   (цель 1:1)")
    print(f"  Sq максимум {max(r[5] for r in rows)}   (лимит протокола 21 подряд)")
    for typ in ("PoW","PoS"):
        s=[r for r in rows if r[0]==typ]
        if len(s)<4: continue
        e=[y[2]-x[2] for x,y in zip(s,s[1:])]
        dr=[(r[3]-b0)-(r[2]-t0) for r in s]
        d0,d1=s[0][4],s[-1][4]
        print(f"  {typ}: {len(s):>4} бл | интервал ср {st.mean(e):6.1f} мед {st.median(e):6.1f} "
              f"ст.откл {st.pstdev(e):6.1f} (цель 120)")
        print(f"       | сложность {d0:.3g} -> {d1:.3g}, рост x{d1/max(1,d0):.2f} "
              f"| дрейф метки медиана {st.median(dr):+.0f} с")

print(f"блоков {len(R)}, высота до {R[-1][1]}, симвремя {R[-1][2]/3600:.2f} ч")
block(R, "ВЕСЬ ПРОГОН, включая разгон")
block([r for r in R if r[1] >= cut], f"ПОСЛЕ ПРОГРЕВА, с высоты {cut}")
PY

echo
echo "=== отказы ==="
printf "  PoW отвергнуто по медиане меток: %s\n" "$(grep -c 'less than median of last 60 blocks' "$L")"
grep -oiE "coinstake age is: [0-9]+ is less than minimum expected: [0-9]+" "$L" | sort | uniq -c

echo "=== агенты ==="
for f in "$H"/python3*.stdout; do
  [ -f "$f" ] || continue
  id=$(grep -m1 -oE '^\[[a-z0-9-]+\]' "$f" | tr -d '[]')
  case "$id" in
    pow-*)  printf "  %s: принято %s, отвергнуто %s\n" "$id" \
              "$(grep -c 'блок #' "$f")" "$(grep -c 'отказ' "$f")" ;;
    dist-*) printf "  %s\n" "$(tail -1 "$f")" ;;
    *)      printf "  %s: %s строк\n" "${id:-неопознан}" "$(wc -l < "$f")" ;;
  esac
done

echo "=== стейкеры ==="
python3 - "$H" <<'PY'
import glob, os, re, sys, collections, statistics as st
h = sys.argv[1]
built = collections.Counter(); funded = 0
for f in sorted(glob.glob(os.path.join(h, "simplewallet.*.stdout"))):
    txt = open(f, errors="ignore").read()
    n = txt.count("has been constructed, sending to core")
    a = re.findall(r"entries with total amount: ([0-9.]+)", txt)
    if a and float(a[-1]) > 0: funded += 1
    if n: built[os.path.basename(f)] = n
total = sum(built.values())
print(f"  кошельков с ненулевым стейком: {funded}")
print(f"  из них построили блоки: {len(built)}, всего построений {total}")
if built:
    v = sorted(built.values(), reverse=True); exp = total/len(built)
    sigma = exp ** 0.5
    print(f"  на стейкера: макс {v[0]}, медиана {v[len(v)//2]}, мин {v[-1]}, ожидание {exp:.1f}")
    print(f"  максимум отстоит от ожидания на {(v[0]-exp)/sigma:.2f} сигмы "
          f"(пуассоновский разброс, до ~2 при таком числе кошельков — норма)")
PY
