L=/tmp/zpos/n1/zanod.log
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
import statistics as st
R=[l.split('\t') for l in open('/tmp/blocks.tsv').read().splitlines()]
R=[(t,int(h),float(s),int(b),int(d),int(q)) for t,h,s,b,d,q in R]
t0,b0=R[0][2],R[0][3]
n=len(R); iv=[y[2]-x[2] for x,y in zip(R,R[1:])]
print(f"блоков {n}, высота до {R[-1][1]}, симвремя {R[-1][2]/3600:.2f} ч")
print(f"СУММАРНЫЙ ТАКТ: среднее {st.mean(iv):.1f} с, медиана {st.median(iv):.1f} с   (цель 60)")
ps=sum(1 for r in R if r[0]=='PoS'); pw=n-ps
print(f"СООТНОШЕНИЕ PoS:PoW = {ps}:{pw} = {ps/max(1,pw):.2f}:1   (цель 1:1)")
print(f"Sq максимум {max(r[5] for r in R)}   (лимит протокола 20)")
for typ in ("PoW","PoS"):
    s=[r for r in R if r[0]==typ]
    if len(s)<9: continue
    print(f"\n--- {typ}, {len(s)} блоков (цель 120 с) ---")
    k=max(1,len(s)//8)
    for i in range(0,len(s),k):
        ch=s[i:i+k]
        if len(ch)<3: continue
        e=[y[2]-x[2] for x,y in zip(ch,ch[1:])]
        dr=[(r[3]-b0)-(r[2]-t0) for r in ch]
        print(f"  бл {ch[0][1]:>4}..{ch[-1][1]:>4}  интервал ср {st.mean(e):7.1f} мед {st.median(e):7.1f}"
              f"  сложность {ch[-1][4]:9.3g}  дрейф метки {st.median(dr):+6.0f} с")
    e=[y[2]-x[2] for x,y in zip(s,s[1:])]
    print(f"  ФОРМА (весь прогон): среднее {st.mean(e):.1f}, ст.откл {st.pstdev(e):.1f} "
          f"(должны совпасть), медиана {st.median(e):.1f} против 0,693×среднего = {0.693*st.mean(e):.1f}")
PY
echo "=== отказы ==="
grep -c "less than median of last 60 blocks" "$L"
grep -oiE "coinstake age is: [0-9]+ is less than minimum expected: [0-9]+" "$L" | sort | uniq -c
D=$(ls ~/zanosim/shadow.data/hosts/zano1/python3.12.*.stdout | head -1)
printf "драйвер PoW: принято %s, отвергнуто %s\n" "$(grep -c 'блок #' $D)" "$(grep -c 'отказ' $D)"
S=$(ls ~/zanosim/shadow.data/hosts/zano1/simplewallet.*.stdout)
echo "=== последние final_diff у кошелька ==="
grep -A1 "Found kernel: amount" "$S" | grep "difficulty:" | tail -3
