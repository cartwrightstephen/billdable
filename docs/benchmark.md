# Benchmark (Oct 13, 2026), measured, not estimated

Run with `python3 tools/bench.py sqlite` and `python3 tools/bench.py json`. It starts the server
in-process on a temporary data folder. One company grows to 100, 200 and 400 jobs (80% closed
history, 20% open); then ten small companies save at the same time.

Machine: a 10-core Apple laptop (arm64), Python 3.9. The improvement plan's numbers (Oct 12)
came from a 4 vCPU Linux machine on Python 3.13, so compare the shape, not the exact
milliseconds.

## One company growing

| Jobs | Page load (`/api/state`) | Read, median | Save, median |
|---|---|---|---|
| Oct 12, before P2-5/P2-6 | | | |
| 100 | 179 KB | 6.6 ms | 15 ms |
| 200 | 338 KB | 12.2 ms | 21 ms |
| 400 | 656 KB | 22.1 ms | 37.7 ms |
| Now, SQLite (the default) | | | |
| 100 | 65 KB | 5.6 ms | 10.0 ms |
| 200 | 113 KB | 9.3 ms | 17.2 ms |
| 400 | 208 KB | 17.4 ms | 32.4 ms |
| Now, JSON files (`DURACASH_STORAGE=json`) | | | |
| 100 | 65 KB | 4.4 ms | 12.9 ms |
| 200 | 113 KB | 7.6 ms | 23.1 ms |
| 400 | 208 KB | 14.2 ms | 43.0 ms |

- **The page load is a third of what it was** (P2-6). It no longer carries closed history, the
  Outbox or the Audit. What is left grows with *open* jobs: 400 jobs here means 80 open, more
  than a 2–12-person contractor runs.
- **Saves still grow with the company**, more slowly on SQLite. SQLite writes only the rows that
  changed, but every request still loads the whole company and builds its page data. At 400
  jobs a read is about 7 ms loading, 8 ms building the page data, 1 ms encoding.
- Two fixes from this measurement: a save no longer loads the company a second time to look
  for queued emails (40 → 32 ms at 400 jobs), and the app no longer asks for the whole state
  again after every action: the change's own answer carries it (one request per action, not two).

## Ten companies saving at once (20 saves each, small companies)

| Storage | Saves per second | Errors |
|---|---|---|
| SQLite | 241 | 0 |
| JSON files | 367 | 0 |

Each company has its own lock (P2-5), so companies don't wait for each other in Duracash. SQLite
still allows one writer at a time across the database and waits for the disk on every commit
(`synchronous=FULL`, chosen because this is a money record), so many small companies write
faster as separate JSON files. Before P2-5, one lock serialized every company's writes; the plan
measured 81 saves per second for ten clients.

## What this means

- Both layouts are far beyond a pilot of tens of companies. The plan's own trigger for Postgres
  ("when a single box is no longer enough, measured") is nowhere near.
- If reads ever need to be faster, the next step is a per-company in-memory copy of the loaded
  company, checked against the stored version (about 7 ms of a 17 ms read at 400 jobs). Measure
  first; it is not needed now.
- `synchronous=NORMAL` would make SQLite writes faster but can lose the last commit on a power
  cut. Not worth it for pay apps and payments.
