"""The growth benchmark from the improvement plan, on the current code (docs/benchmark.md).

    python3 tools/bench.py sqlite      (or: json)

One company with 100, 200 and 400 jobs (80% closed history, 20% open): the size and time of
the page load (/api/state) and the time of one save. Then ten companies saving at once.
Runs a server in-process on a temporary data folder; nothing else is touched."""
import http.client, json, os, statistics, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
backend = sys.argv[1]
os.environ["BILLDABLE_STORAGE"] = "json" if backend == "json" else ""
import auth, server, storage, domain
from seed import seed_state
tmp = tempfile.TemporaryDirectory()
root = Path(tmp.name)
auth.AUTH_PATH, server.COMPANIES, storage.DATA_DIR = root / "auth.json", root / "companies", root
httpd = server.Server(("127.0.0.1", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]
def req(method, path, body=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    h = {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})}
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse(); raw = r.read(); c.close(); return r, raw
r, raw = req("POST", "/api/auth/signup", {"accept_terms": True, "email": "b@example.com", "password": "correct horse battery", "name": "B", "company_name": "Bench"})
cookie = r.getheader("Set-Cookie").split(";")[0]; cid = json.loads(raw)["user"]["company_id"]
base = seed_state()
template_open = next(j for j in base["jobs"] if not j.get("closed"))
template_closed = next(j for j in base["jobs"] if j.get("closed"))
def grow(n):
    st = seed_state()
    for i in range(n):
        t = template_closed if i % 5 else template_open   # 80% closed history, 20% open, like an older company
        j = json.loads(json.dumps(t)); j["id"] = f"job-b{i}"; j["code"] = f"B{i}"; j["name"] = f"Bench job {i}"
        if j.get("closed"): j["closed_at"] = "2025-01-01T00:00:00+00:00"
        st["jobs"].append(j)
        domain.audit(st, j["id"], "note", f"Bench row {i}", "bench")
    server.save_company(cid, st)
for n in (100, 200, 400):
    grow(n)
    sizes, gets = [], []
    for _ in range(15):
        t = time.perf_counter(); r, raw = req("GET", "/api/state", cookie=cookie); gets.append((time.perf_counter() - t) * 1000); sizes.append(len(raw))
    posts = []
    job = next(j for j in json.loads(raw)["jobs"] if not j.get("closed"))
    for k in range(15):
        t = time.perf_counter(); r, _ = req("POST", f"/api/jobs/{job['id']}/add-record", {"label": f"note {k}", "kind": "note"}, cookie=cookie)
        posts.append((time.perf_counter() - t) * 1000); assert r.status == 200
    print(f"{backend:6} {n} jobs: /api/state {sizes[-1]//1024} KB, GET median {statistics.median(gets):.1f} ms, save median {statistics.median(posts):.1f} ms")

# Ten companies saving at the same time: each company has its own lock, so they don't queue.
cookies = []
for n in range(10):
    r, raw = req("POST", "/api/auth/signup", {"accept_terms": True, "email": f"c{n}@example.com", "password": "correct horse battery", "name": "C", "company_name": f"C{n}"})
    c = r.getheader("Set-Cookie").split(";")[0]
    server.save_company(json.loads(raw)["user"]["company_id"], seed_state())
    cookies.append(c)
def writer(c, out):
    for k in range(20):
        r, _ = req("POST", "/api/jobs/job-inman/add-record", {"label": f"n{k}", "kind": "note"}, cookie=c); out.append(r.status)
statuses = []
t = time.perf_counter()
threads = [threading.Thread(target=writer, args=(c, statuses)) for c in cookies]
[th.start() for th in threads]; [th.join() for th in threads]
took = time.perf_counter() - t
print(f"{backend:6} 10 companies x 20 saves at once: {len(statuses) / took:.0f} saves/s, {statuses.count(200)}/{len(statuses)} ok")
