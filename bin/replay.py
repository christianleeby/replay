#!/usr/bin/env python3
"""Class replays, automatic (his ask, Oct 28 2026: "a system where you're doing it after every class I teach
without being asked").

Every Skool livestream page links ONCE to a permanent page — christianleeby.github.io/replay/<slot>/ — that
forwards to the newest Zoom cloud recording of that class. Nobody edits Skool after class again.

  replay.py            background (launchd, every 20 min): ask Zoom for each class meeting's newest recording;
                       when a new one has finished processing → rewrite that slot's page in the replay site,
                       git push (GitHub Pages serves it), phone ping via ntfy, and mark it "pending" for the texts.
  replay.py --apply    in-repo (alerts/check.py runs it every session): for each pending replay, run
                       classes/attendance.py replay <date> <slot> --link <permanent page> so the missed-class
                       texts on Reach Outs fill in. If the class isn't logged yet it waits and says so.
  replay.py --install  in-repo: copy this script to ~/.config/replay/bin, clone the site, load the launchd job.
  replay.py --status   what each slot points at now.

RUNS IN THE CLOUD (his yes, Oct 28: "run with the laptop closed"): the background sweep is a GitHub Actions
schedule in the replay repo (.github/workflows/replays.yml, every 20 min) running bin/replay.py there, with the
Zoom keys + ntfy topic as repo Secrets. State lives in the site repo (state.json) so the cloud and the Mac share it.
--install copies this script into the local site clone (~/.config/replay/site) and pushes it — run it after any
edit here. The old launchd job is retired.
Local Zoom keys (for a manual run): ~/.config/ypp/zoom.env. Recording links have the passcode embedded (Zoom
setting ticked Oct 28), so no passcode step.
"""
import json, os, sys, shutil, base64, subprocess, datetime, pathlib, urllib.request, urllib.parse
from zoneinfo import ZoneInfo

HOME = pathlib.Path.home()
BASE = HOME / ".config/replay"
CLOUD = bool(os.environ.get("GITHUB_ACTIONS"))
SITE = pathlib.Path(os.environ["GITHUB_WORKSPACE"]) if CLOUD else BASE / "site"
STATE = SITE / "state.json"
ENV = HOME / ".config/ypp/zoom.env"
TOPIC_F = HOME / ".config/new-sale-texter/topic"
REMOTE = "https://github.com/christianleeby/replay.git"
PUBLIC = "https://christianleeby.github.io/replay"
TZ = ZoneInfo("America/Denver")
EXPIRE_DAYS = 4  # same window the replay texts promise (attendance.py default)

# slug → (Zoom meeting ID, roster slot label, page title)
SLOTS = {
    "tuesday":       ("83000304949", "Tuesday 6:00",   "Tuesday 6:00pm"),
    "wednesday":     ("86713407390", "Wednesday 6:00", "Wednesday 6:00pm"),
    "thursday":      ("87943303212", "Thursday 11:00", "Thursday 11:00am"),
    "saturday-945":  ("87297458250", "Saturday 9:45",  "Saturday 9:45am"),
    "saturday-1045": ("83106149309", "Saturday 10:45", "Saturday 10:45am"),
}

HERE = pathlib.Path(__file__).resolve()
IN_REPO = HERE.parent.name == "replays" and HERE.parents[1].name == "skills"
ROOT = HERE.parents[3] if IN_REPO else None


def env():
    if os.environ.get("ZOOM_CLIENT_ID"):
        return os.environ
    out = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def token():
    e = env()
    req = urllib.request.Request(
        "https://zoom.us/oauth/token?" + urllib.parse.urlencode(
            {"grant_type": "account_credentials", "account_id": e["ZOOM_ACCOUNT_ID"]}), method="POST")
    req.add_header("Authorization", "Basic " + base64.b64encode(
        f'{e["ZOOM_CLIENT_ID"]}:{e["ZOOM_CLIENT_SECRET"]}'.encode()).decode())
    return json.load(urllib.request.urlopen(req, timeout=20))["access_token"]


def latest(tok, meeting):
    req = urllib.request.Request(f"https://api.zoom.us/v2/meetings/{meeting}/recordings")
    req.add_header("Authorization", f"Bearer {tok}")
    try:
        d = json.load(urllib.request.urlopen(req, timeout=20))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # no recording for this meeting yet
        raise
    files = d.get("recording_files") or []
    if not d.get("share_url") or not files or any(f.get("status") != "completed" for f in files):
        return None  # still processing
    url = d["share_url"]
    if d.get("password") and d.get("recording_play_passcode") and "pwd=" not in url:
        url += ("&" if "?" in url else "?") + "pwd=" + d["recording_play_passcode"]
    start = datetime.datetime.fromisoformat(d["start_time"].replace("Z", "+00:00")).astimezone(TZ)
    return {"start": d["start_time"], "date": start.date().isoformat(), "url": url,
            "minutes": d.get("duration")}


def page(title, rec):
    exp = (datetime.date.fromisoformat(rec["date"]) + datetime.timedelta(days=EXPIRE_DAYS)).isoformat()
    day = datetime.date.fromisoformat(rec["date"]).strftime("%a %b %-d")
    good = (datetime.date.fromisoformat(exp) - datetime.timedelta(days=1)).strftime("%A, %b %-d")
    url = rec["url"].replace("&", "&amp;").replace('"', "&quot;")
    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=robots content=noindex>
<title>Body Fixing Yoga · {title} class replay</title>
<style>body{{font:18px/1.5 -apple-system,Segoe UI,sans-serif;max-width:560px;margin:15vh auto;padding:0 16px;
color:#2d1b3d;background:#faf7f2;text-align:center}}a{{color:#5b2a86;font-weight:600}}</style>
<h2>{title} class replay</h2>
<p id=m>Class from {day}. Opening the recording…<br><a href="{url}">Tap here if it doesn't open</a></p>
<script>
if (new Date() >= new Date("{exp}T00:00:00-07:00")) {{
  document.getElementById("m").innerHTML = "This replay (from {day}) has expired. The next class's replay goes up here after class.";
}} else {{ location.replace("{rec['url']}"); }}
</script>
<noscript><p>Good through {good}.</p></noscript>
</html>
"""


PLACEHOLDER = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=robots content=noindex>
<title>Body Fixing Yoga · {title} class replay</title>
<style>body{{font:18px/1.5 -apple-system,Segoe UI,sans-serif;max-width:560px;margin:15vh auto;padding:0 16px;
color:#2d1b3d;background:#faf7f2;text-align:center}}</style>
<h2>{title} class replay</h2><p>The replay goes up here after class.</p></html>
"""


def git(*a):
    return subprocess.run(["git", "-C", str(SITE), *a], capture_output=True, text=True)


def ping(title, msg):
    try:
        topic = os.environ.get("NTFY_TOPIC") or TOPIC_F.read_text().strip()
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=msg.encode(), method="POST")
        req.add_header("Title", title.encode("utf-8").decode("latin-1", "ignore"))
        req.add_header("Tags", "movie_camera")
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print(f"ping failed: {e}")


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1))


def sweep(seed=False):
    """Background run. seed=True (first install): publish what's there now, but don't queue texts or ping."""
    state, tok, changed = load_state(), token(), []
    git("pull", "-q", "--rebase", "origin", "main")
    for slug, (meeting, slot, title) in SLOTS.items():
        rec = latest(tok, meeting)
        if not rec or state.get(slug, {}).get("start") == rec["start"]:
            continue
        (SITE / slug).mkdir(parents=True, exist_ok=True)
        (SITE / slug / "index.html").write_text(page(title, rec))
        state[slug] = {**rec, "slot": slot, "published": datetime.datetime.now().isoformat(timespec="minutes"),
                       "pending": not seed}
        changed.append((slug, slot, rec))
    for slug, (_, _, title) in SLOTS.items():  # a slot with no recording yet still gets a page (Skool links it day one)
        if not (SITE / slug / "index.html").exists():
            (SITE / slug).mkdir(parents=True, exist_ok=True)
            (SITE / slug / "index.html").write_text(PLACEHOLDER.format(title=title))
            changed.append((slug, None, None))
    if not changed:
        print("replays: nothing new")
        return
    STATE.write_text(json.dumps(state, indent=1))
    if not (SITE / "index.html").exists():
        (SITE / "index.html").write_text("<!doctype html><meta charset=utf-8><title>Replays</title>"
                                         "<meta name=robots content=noindex><p>Body Fixing Yoga class replays.</p>")
    git("add", "-A")
    git("commit", "-q", "-m", "Replay: " + ", ".join(f"{s} {r['date'] if r else 'placeholder'}" for s, _, r in changed))
    p = git("push", "-q", "origin", "HEAD:main")
    if p.returncode:
        print(f"push failed: {p.stderr.strip()}")
        return  # state not pushed → retried next run
    for slug, slot, rec in changed:
        if not rec:
            continue
        print(f"🎥 {slot} {rec['date']} → {PUBLIC}/{slug}/")
        if not seed:
            ping(f"{slot} replay is up",
                 f"Class {rec['date']} ({rec['minutes']} min) is live on Skool. Missed-class texts fill in at your next Noel session.")


def apply():
    """In-repo: fill the missed-class texts for every pending replay."""
    git("pull", "-q", "--rebase", "origin", "main")
    state = load_state()
    todo = [(k, v) for k, v in state.items() if v.get("pending")]
    if not todo:
        return
    att = ROOT / ".claude/skills/classes/attendance.py"
    for slug, v in todo:
        exp = (datetime.date.fromisoformat(v["date"]) + datetime.timedelta(days=EXPIRE_DAYS)).isoformat()
        r = subprocess.run([sys.executable, str(att), "replay", v["date"], v["slot"],
                            "--link", f"{PUBLIC}/{slug}/", "--expires", exp], capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            v["pending"] = False
            print(out.splitlines()[0] if out else f"🎥 {v['slot']} {v['date']} texts filled")
        else:
            print(f"🎥 {v['slot']} {v['date']} replay is live — texts wait: {out.splitlines()[-1] if out else 'error'}")
    save_state(state)
    git("commit", "-q", "-m", "Replay texts filled", "--", "state.json")
    git("push", "-q", "origin", "HEAD:main")


def install():
    if not (SITE / ".git").exists():
        subprocess.run(["git", "clone", "-q", REMOTE, str(SITE)], check=True)
    git("pull", "-q", "--rebase", "origin", "main")
    (SITE / "bin").mkdir(exist_ok=True)
    shutil.copy2(HERE, SITE / "bin/replay.py")
    plist = HOME / "Library/LaunchAgents/com.christianleeby.replays.plist"
    if plist.exists():  # retired: the cloud runs it now
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        plist.unlink()
    git("add", "-A")
    git("commit", "-q", "-m", "replay.py updated")
    print(git("push", "-q", "origin", "HEAD:main").stderr.strip() or "installed → replay repo (cloud runs it every 20 min)")


def status():
    for slug, v in load_state().items():
        print(f"{v['slot']:15} {v['date']}  {'⏳ texts pending' if v.get('pending') else '✓'}  {PUBLIC}/{slug}/")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--install" in a:
        install()
        if "--seed" in a:
            sweep(seed=True)
    elif "--apply" in a:
        apply()
    elif "--status" in a:
        status()
    else:
        sweep()
