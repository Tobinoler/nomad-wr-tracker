# Nomad WR Tracker

Weight-room performance tracking for **Nomad Baseball**. Athletes log lifts, jumps
and sprints on a shared tablet or their own phone; every number lands in a
persistent per-athlete profile that a coach can review and rank.

Built with [Shiny for Python](https://shiny.posit.co/py/). No database — three
flat CSVs you can open in Excel, edit by hand, and back up by copying a folder.

---

## The four pages

| Page | Who uses it | What it does |
| --- | --- | --- |
| **Quick Entry** | Athletes, mid-session | Pick athlete → pick lift → see their last value and PR → type the number → Save. Beats their PR? Gold celebration banner. New athletes add themselves here. |
| **Athlete** | Athlete or coach | Every metric they've logged: PR, latest, change since first entry, a trend chart per metric, and the full entry log with PRs flagged. |
| **Team** | Coach | Leaderboard per metric across the active roster, filterable by class, with optional Elite/Advanced/Average/Below-Average tiers. |
| **Admin** | Coach | Add/edit/deactivate athletes, add/edit metrics, set benchmark cutoffs. |

---

## Quick start

```bash
cd NomadWRTracker
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Then open **http://localhost:8000**.

On first run the app creates `data/` and seeds `metrics.csv` with the 10 metrics
below. **The roster starts empty** — `athletes.csv`, `entries.csv` and
`benchmarks.csv` contain nothing but headers, because the app never invents
athletes, results or standards.

### Adding athletes

**Athletes add themselves from Quick Entry.** Under the athlete picker there's a
quiet "Not on the list? **+ Add me**" — it opens a small form (name, grad year,
up to two positions) and nothing else. Submitting puts them on the roster *and
selects them*, so the next tap is their first lift. That's the whole reason it
exists: nobody needs to be sent to the Admin page, where the same tap could edit
the metric catalogue and change PR detection for the entire team.

Tapping "Add me" with a name already on the roster does **not** create a second
row — it selects the athlete who's already there and says so. The name check and
the write happen inside one lock, so two people signing up at the same instant
can't both win, and matching ignores case and stray spaces (`  mike  ` finds
`Mike`). What it can't catch is the same athlete entering a genuinely different
name (`Jack S` vs `Jack Snakenberg`); those are two people as far as the app is
concerned, and a coach merges them by editing `athletes.csv`.

Coaches can still do all of it from Admin → **Athletes**, which additionally
allows editing and deactivating. New athletes appear on every connected phone
within a second, no refresh needed.

`shiny run --host 0.0.0.0 --port 8000 app.py` does the same thing; add
`--reload` while you're editing code.

### Demo data

To see charts and leaderboards with something in them. Needs at least one
athlete on the roster first — it generates history for the athletes it finds:

```bash
python seed_demo.py                    # ~450 entries over the last 16 weeks
python seed_demo.py --with-benchmarks  # plus PLACEHOLDER tier cutoffs
```

**Before your first real session, clear it:**

```bash
python seed_demo.py --reset
```

The `--with-benchmarks` numbers are made-up placeholders so you can see the tier
colours working. Replace them with Nomad's own standards on the Admin page — or
leave cutoffs blank entirely, in which case leaderboards just show raw rankings.

---

## Put it on the weight-room Wi-Fi

`python app.py` already binds to `0.0.0.0`, meaning "accept connections from any
device on this network", not just this computer. On startup it prints the exact
address to hand out:

```
Nomad WR Tracker starting
  This machine : http://localhost:8000
  Phones/tablets on the same Wi-Fi: http://192.168.1.83:8000
```

Everyone on the same Wi-Fi opens that second URL. To find the address yourself:

- **Windows:** `ipconfig` → "IPv4 Address" under your Wi-Fi adapter
- **macOS:** `ipconfig getifaddr en0`
- **Linux:** `hostname -I`

Practical notes:

- **Firewall.** The first time you run it, Windows will ask whether to allow
  Python through the firewall — allow it on **Private networks**. Without that,
  phones get a spinning wheel. (If you missed the prompt: Windows Security →
  Firewall & network protection → Allow an app through firewall → Python.)
- **Pin the address.** Router DHCP can hand the kiosk a different IP after a
  reboot. Reserve a static IP for the weight-room machine in your router, or
  just re-read the address off the startup log.
- **Home-screen icon.** On iOS/Android, open the URL and choose "Add to Home
  Screen" — it launches full-screen like an app.
- **Keep the tablet awake.** Set the kiosk's screen timeout to Never, or every
  athlete pays a wake-and-unlock tax before logging a set.
- **It's an open app.** Anyone on the Wi-Fi who has the URL can log entries and
  edit the roster — there's no login. That's deliberate for a gym kiosk. Don't
  expose port 8000 to the internet; if you need it off-site, use one of the
  hosted options below or a VPN.

---

## The data files

Everything lives in `data/` (override with the `NOMAD_WR_DATA` environment
variable, e.g. to point at a shared drive or a Connect volume).

**`athletes.csv`** — roster, changes rarely

```csv
athlete_id,name,grad_year,position,active,created_at
ath_92d9b6e5f5b7,Matthew Heredia,2028,SS / RHP,true,2026-08-19T15:50:21
```

`position` is free text; the Admin form builds it from a primary and an optional
second position joined with ` / `. Deactivating an athlete (rather than deleting
the row) keeps their history on file but drops them off leaderboards and out of
the Quick Entry picker — the right move when a class graduates.

**`metrics.csv`** — catalogue of trackable things

```csv
metric_id,name,category,unit,higher_is_better
back_squat,Back Squat,Strength,lbs,true
flying_10_sprint,Flying 10 Sprint,Speed,sec,false
```

`higher_is_better` drives PR detection and tier logic, so it must be right:
`true` for a squat or a vertical, `false` for a timed sprint. Categories are
Strength / Jump / Speed / Power / Other; units are free text (lbs, in, cm, sec,
mph, watts…).

**`entries.csv`** — the log, one row per number, append-only

```csv
entry_id,timestamp,athlete_id,metric_id,value,notes
ent_bd1ee6d60450,2026-08-19T15:20:03,ath_9a32d7212813,back_squat,325,3x5 felt easy
```

Long/tidy format on purpose: adding a new metric never changes the schema, it's
just a new row in `metrics.csv`.

**`benchmarks.csv`** — optional tier cutoffs

```csv
metric_id,elite,advanced,average
back_squat,405,315,245
```

Each number is the *minimum* to reach that tier (for a lower-is-better metric
it's the maximum). Leave a cell blank to skip a tier; leave a metric out
entirely and its leaderboard shows a plain ranking with no tier colours.

Timestamps are ISO 8601 local wall-clock time. IDs are generated for you —
athletes and entries get UUID-based ids, metrics get a readable slug
(`back_squat`) so `benchmarks.csv` is legible when you edit it by hand.

### Seeded metrics

Back Squat, Front Squat, Trap Bar Deadlift, Bench Press, Weighted Chin-Up (lbs) ·
Vertical Jump, Broad Jump (in) · Flying 10 Sprint (sec, **lower is better**) ·
Overhead Med Ball Throw (mph) · Body Weight (lbs).

Edit `nomad_wr/config.py` to change what a *brand-new* installation seeds (and
the position list the Admin dropdowns offer); once the CSVs exist, the app only
reads them, so use the Admin page instead. `SEED_ATHLETES` is deliberately empty
so a lost `athletes.csv` can't resurrect example athletes onto a real
leaderboard.

---

## How it survives two phones hitting Save at once

All file access goes through `nomad_wr/storage.py`:

- **Entries are appended, never rewritten.** Recording a lift writes exactly one
  line. A crash mid-write can cost the row being written — never the thousands
  already on disk.
- **Every write takes a cross-process lock** (`filelock`) on `data/.nomad_wr.lock`,
  so simultaneous saves from the kiosk and a phone queue up instead of
  interleaving into a corrupt line.
- **Catalogue edits are atomic.** `athletes.csv`, `metrics.csv` and
  `benchmarks.csv` are re-read inside the lock, edited, written to a temp file,
  `fsync`-ed, and `os.replace`-d over the original. A reader sees the whole old
  file or the whole new one, never a half-written one.
- **Reads never crash the app.** A malformed row is skipped and logged; a row
  with an unreadable timestamp keeps its value. One bad line can't take down the
  kiosk mid-session.
- **PR detection is atomic.** The history read and the append happen in one lock
  acquisition, so two devices can't both be told "New PR!" for the same number.
- **Live refresh.** Each CSV is polled for changes, so a save on the kiosk shows
  up on every connected phone within a second — including edits you make to a
  CSV by hand.

`python tests/test_storage.py` exercises all of it, including 120 appends from
four concurrent processes, and asserts nothing is lost or interleaved. It runs
against a temp directory, never your real `data/`. (`pytest tests/` works too.)

---

## Hosted deployment

The app runs fine as-is on the gym LAN: the data sits on a machine you control
and works when the internet doesn't. Posit Connect adds outside access and
logins on top, and — unlike the hosted alternatives below — has a persistent
filesystem, so the CSV store keeps working.

### Posit Connect, deployed from GitHub

The repo carries a `manifest.json`, which is what Connect's git-backed
publishing needs. In Connect: **Content → Publish → Import from Git**, then give
it the repo URL, the branch (`main`), and `.` as the directory holding the
manifest. Connect polls the repo and redeploys on each new commit — you ship by
pushing.

For a **private** repo, Connect needs read access to it. That's an admin-side
setting on the Connect server (a deploy key or credentials in `rsconnect.gcfg`
under `[Git]`), not something the publishing user can set.

Then, on the content item:

1. **Vars → add `NOMAD_WR_DATA`**, pointing at a durable path *outside* the
   bundle, because Connect replaces the bundle directory on every deploy:

   ```
   NOMAD_WR_DATA=/data/nomad-wr
   ```

   Create that directory, make it writable by the Connect run-as user, and copy
   your current `data/*.csv` into it before the first launch. Skip this and the
   app still runs — it just starts empty again after each redeploy.

2. **Runtime → Max processes.** Several processes on one host are fine; the
   `filelock` guard is cross-process. What it can't coordinate is several
   *hosts* in an HA cluster writing to the same NFS mount, since `flock` over
   NFS isn't dependable. On a clustered Connect, hold this at one process, or
   move `storage.py` onto a database.

3. **Access.** Connect gates the whole app, including Admin. If athletes are
   meant to log their own lifts from outside the gym, they each need a Connect
   account with viewer access.

Python: the manifest asks for **3.11** (see `.python-version`). If your Connect
server doesn't have 3.11 installed, change `python.version` in `manifest.json`
to one it does — the code is verified clean on 3.9 through 3.14.

**If you add a new file** (a new page module, say), regenerate the manifest —
Connect bundles the files it lists, so an unlisted file simply won't deploy:

```bash
rsconnect write-manifest shiny . --entrypoint app:app --overwrite \
  --exclude data --exclude tests --exclude "**/__pycache__" --exclude "*.pyc"
```

That rewrites `python.version` to whatever Python you ran it with, so set it
back to 3.11 (or your server's version) afterwards. Editing existing files
doesn't need this.

The push-based route works too, if you'd rather not wire up git:

```bash
pip install rsconnect-python
rsconnect deploy shiny . \
  --server https://connect.your-org.com --api-key <KEY> \
  --entrypoint app:app --title "Nomad WR Tracker"
```

### shinyapps.io / Connect Cloud — demo only

Both give each instance an **ephemeral filesystem**. Anything written to `data/`
is lost when the instance sleeps, restarts, or redeploys, and two instances
don't share a disk, so the file lock can't protect you either. Fine for showing
the app off; not a system of record. To use either for real, swap the body of
`storage.py` for a durable backend (S3, Google Sheets, a small Postgres). The
rest of the app talks only to that module, so nothing else changes.

---

## Project layout

```
NomadWRTracker/
├─ app.py                    # entry point: navbar, page wiring, LAN banner
├─ seed_demo.py              # demo history generator / --reset
├─ manifest.json             # Posit Connect git-backed deploy descriptor
├─ .python-version           # Python version Connect is asked for
├─ requirements.txt
├─ nomad_wr/
│  ├─ config.py              # paths, colours, seed lists
│  ├─ storage.py             # ALL csv read/write/lock logic lives here
│  ├─ data.py                # reactive, auto-refreshing views of the CSVs
│  ├─ logic.py               # PRs, tiers, leaderboards, formatting (no IO)
│  ├─ charts.py              # plotly figures → inline HTML
│  ├─ ui_helpers.py          # tiles, tables, badges, alerts
│  └─ pages/                 # one Shiny module per page
│     ├─ quick_entry.py
│     ├─ athlete_profile.py
│     ├─ team_dashboard.py
│     └─ admin.py
├─ www/styles.css            # Nomad navy theme, mobile-first
├─ data/                     # created on first run
└─ tests/test_storage.py
```

Charts use Plotly's JavaScript bundle, copied out of the installed `plotly`
package into `www/` at startup rather than loaded from a CDN — so charts render
on a gym network with no internet.

---

## Known limits

- **No login.** Anyone on the network can log an entry, add themselves, or reach
  Admin and edit the roster and metrics. Fine for a kiosk on a private Wi-Fi;
  not fine on the open internet. (Posit Connect puts real auth in front of it.)
- **No entry editing or deletion in the UI.** The log is append-only by design.
  A fat-fingered value is flagged at save time ("that's far off their usual
  numbers") but still recorded. To fix one: stop the app and delete the row from
  `entries.csv`, or just log the correct value — leaderboards and PRs use each
  athlete's best, and the profile chart shows the whole history.
- **One facility, one timezone.** Timestamps are local wall-clock with no offset.
- **Roster scale.** Built and styled for ~40–90 athletes. It'll work with more;
  the athlete picker just gets more scrolling.
