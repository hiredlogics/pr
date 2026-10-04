from pathlib import Path

for name in (".env", ".env.local"):
    p = Path(name)
    if not p.exists():
        continue
    raw = p.read_text(encoding="utf-8-sig", errors="ignore")
    print("FILE", name, "bytes", p.stat().st_size)
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip("\"'")
        print(f"  {k}: {'nonempty' if v else 'empty'} len={len(v)}")
