#!/usr/bin/env python3
import re, sys
from pathlib import Path

GEN  = Path.home() / "zanogen"
SRC  = Path.home() / "zano" / "src" / "currency_core"
ARR  = (GEN / "genesis_source.json.genesis.uint64.array.txt").read_text()
DICT = (GEN / "genesis_source.json.genesis.dictionary.txt").read_text()

# ---- разбор сгенерированного ----
m = re.search(r"uint64_t const v\[(\d+)\];\s*\n\s*uint8_t const r\[(\d+)\];", ARR)
assert m, "не разобраны размеры массива"
NV, NR = m.group(1), m.group(2)

blob = ARR.split("--------- genesis.cpp---------", 1)[1].strip()
assert blob.startswith("const genesis_tx_raw_data"), blob[:60]

pub = re.search(r'ggenesis_tx_pub_key_str = "([0-9a-f]{64})"', DICT).group(1)
dm  = re.search(r"const genesis_tx_dictionary_entry ggenesis_dict\[(\d+)\] = \{(.*?)\n\};",
                DICT, re.S)
assert dm, "не разобран словарь"
ND, ENTRIES = dm.group(1), dm.group(2).strip()

print(f"разобрано: v[{NV}] r[{NR}], словарь на {ND}, ключ {pub[:16]}…")

# ---- обёртка вокруг существующего блока ----
def wrap(fname, payload):
    p = SRC / fname
    t = p.read_text()
    if "ZANO_SIMNET" in t:
        print(f"  {fname}: уже правлен"); return
    i = t.index("#ifndef TESTNET")
    # ищем парный #endif
    depth, j = 0, i
    for mm in re.finditer(r"^#(ifn?def|if|endif)", t[i:], re.M):
        if mm.group(1) == "endif":
            depth -= 1
            if depth == 0:
                j = i + mm.end(); break
        else:
            depth += 1
    assert j > i, f"{fname}: не найден парный #endif"
    new = ("#ifdef ZANO_SIMNET\n" + payload.rstrip() + "\n#else\n"
           + t[i:j] + "\n#endif")
    p.write_text(t[:i] + new + t[j:])
    print(f"  {fname}: обёрнут")

wrap("genesis.h", f"""  struct genesis_tx_raw_data
  {{
    uint64_t const v[{NV}];
    uint8_t const r[{NR}];
  }};""")

wrap("genesis.cpp", "  " + blob)

wrap("genesis_acc.h", f"extern const genesis_tx_dictionary_entry ggenesis_dict[{ND}];")

wrap("genesis_acc.cpp", f"""  const std::string ggenesis_tx_pub_key_str = "{pub}";
  const crypto::public_key ggenesis_tx_pub_key = epee::string_tools::parse_tpod_from_hex_string<crypto::public_key>(ggenesis_tx_pub_key_str);
  const genesis_tx_dictionary_entry ggenesis_dict[{ND}] = {{
{ENTRIES}
  }};""")

# ---- timestamp genesis (из 2.4) ----
p = SRC / "currency_format_utils.cpp"
t = p.read_text()
old = "    bl.timestamp = 0;\n"
if "ZANO_SIMNET" in t:
    print("  currency_format_utils.cpp: уже правлен")
else:
    assert t.count(old) == 1, f"bl.timestamp = 0 встречается {t.count(old)} раз"
    p.write_text(t.replace(old,
        "#ifdef ZANO_SIMNET\n"
        "    bl.timestamp = 946684800;   // 2000-01-01, начало отсчёта часов Shadow\n"
        "#else\n" + old + "#endif\n", 1))
    print("  currency_format_utils.cpp: timestamp = 946684800")

# ---- CACHE_SIZE (пункт 1.6) ----
p = Path.home() / "zano" / "src" / "common" / "db_backend_base.h"
t = p.read_text()
old = "#ifndef ENV32BIT\n"
if "ZANO_SIMNET" in t:
    print("  db_backend_base.h: уже правлен")
else:
    assert old in t
    p.write_text(t.replace(old,
        "#ifdef ZANO_SIMNET\n"
        "#define CACHE_SIZE uint64_t(2UL * 1024UL * 1024UL * 1024UL)   // 2 GiB\n"
        "#elif !defined(ENV32BIT)\n", 1), )
    print("  db_backend_base.h: CACHE_SIZE = 2 ГиБ")

print("\nготово")
