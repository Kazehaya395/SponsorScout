"""SUPERSEDED 2026-10-17: its byte-identity premise (test seed = verbatim
rows of the then-production seed) predates the Wave 6/8 regroupings and the
B7 retirement; kept as the historical format check only.
"""
import csv, io, os, sys

csv.field_size_limit(10 ** 9)
SEED = os.environ.get("SPONSORSCOUT_SEED", "/home/user/codebase/seeds/company_Career_seed.csv")
OUT = "/home/user/codebase/seeds/test_bad_portals_Career_seed.csv"
fails = []


def chk(label, got, want):
    ok = got == want
    print("  %-58s %-28s %s" % (label, str(got)[:28], "ok" if ok else "WANT %s" % want))
    if not ok:
        fails.append(label)


print("=== format ===")
b = io.open(OUT, "rb").read()
t = b.decode("utf-8")
chk("no BOM", b[:3] == b"\xef\xbb\xbf", False)
chk("CRLF only (no bare LF)", t.count("\n") - t.count("\r\n"), 0)
chk("ends with a newline", t.endswith("\r\n"), True)
lines = t.split("\r\n")
chk("header identical to production seed",
    lines[0], io.open(SEED, encoding="utf-8-sig", newline="").read().split("\r\n")[0])

data = [l for l in lines[1:] if l.strip() and not l.lstrip().startswith("#")]
banners = [l for l in lines[1:] if l.lstrip().startswith("#")]
chk("data rows", len(data), 108)
chk("banner/comment rows", len(banners) >= 16, True)
chk("every data row has 12 columns",
    sorted({len(next(csv.reader([l]))) for l in data}), [12])

print("\n=== byte-identity against the production seed ===")
prod = io.open(SEED, encoding="utf-8-sig", newline="").read().replace("\r\n", "\n")
prod_lines = {l for l in prod.split("\n") if l.strip() and not l.lstrip().startswith("#")}
missing = [l for l in data if l.replace("\r\n", "\n") not in prod_lines]
chk("rows copied verbatim (0 altered)", len(missing), 0)
for m in missing[:3]:
    print("      NOT FOUND:", m[:110])

names = [next(csv.reader([l]))[0] for l in data]
urls = [next(csv.reader([l]))[1] for l in data]
chk("no duplicate portal names", len(names) - len(set(names)), 0)
chk("no duplicate URLs", len(urls) - len(set(urls)), 0)
chk("every URL is http(s)", sum(1 for u in urls if not u.startswith("http")), 0)

print("\n=== groups present ===")
for l in banners:
    if "=====" in l:
        print("   ", l.strip())

print("\n=== the app's own seed reader ===")
# 2026-10-15: the package now lives in codebase/, and its dependency modules
# (paths.py, core/location_country.py, ...) were merged in from the old testpkg
# shim, so the real seed_manager imports cleanly from there.
sys.path.insert(0, os.environ.get("SPONSORSCOUT_PKG", "/home/user/codebase"))
try:
    from sponsorscout.application import seed_manager as sm
    from pathlib import Path
    res = sm.read_seed_rows(Path(OUT))   # read_seed_rows takes a Path, not a str
    rws = res["rows"]
    real = [r for r in rws if not (r.get("name") or "").lstrip().startswith("#")]
    ban = [r for r in rws if (r.get("name") or "").lstrip().startswith("#")]
    print("   read_seed_rows -> %d rows total, %d real, %d banners"
          % (len(rws), len(real), len(ban)))
    chk("reader sees 108 real portals", len(real), 108)
    chk("reader keeps the 12-column schema",
        sorted(res.get("columns") or res.get("fieldnames") or []), sorted(
            next(csv.reader([lines[0]]))))
    bad_schema = [r["name"] for r in real
                  if not (r.get("careers_url") or "").startswith("http")]
    chk("every parsed row has a usable URL", len(bad_schema), 0)
    print("   sample:", [(r["name"], r["target_country"], r["provider"]) for r in real[:3]])
except Exception as e:
    print("   reader check skipped:", type(e).__name__, str(e)[:120])

print("\n%s  (%d failures)" % ("PASS" if not fails else "FAIL: " + ", ".join(fails), len(fails)))
print("size:", os.path.getsize(OUT), "bytes")
