"""Self-check of a generated tracker.json against TRACKER.md (counts, IDs, states, progress keys).
usage: python3 -I check.py <tracker.json>   (exit 1 on failure)
"""
import json, re, sys, glob, os
root = os.environ.get("TRACKER_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = json.load(open(sys.argv[1]))
md = open(root + "/TRACKER.md", encoding="utf-8").read()
errs = []
states = ["todo", "in_progress", "done", "dropped", "unknown"]
def tally(tasks):
    c = dict.fromkeys(states, 0)
    for t in tasks:
        c[t["state"] if t["state"] in c else "unknown"] += 1
    return c
def pct(c, n):
    den = n - c["dropped"]
    return round(100 * c["done"] / den, 1) if den else 0.0
tasks = [t for p in d["phases"] for t in p["tasks"]]
s = d["summary"]; c = tally(tasks)
if s["total"] != len(tasks): errs.append("total")
if s["byState"] != c: errs.append("byState")
if abs(s["percentDone"] - pct(c, len(tasks))) > 0.05: errs.append("pct")
if sorted(map(str, s["inProgress"])) != sorted(str(t["id"]) for t in tasks if t["state"] == "in_progress"): errs.append("inProgress")
if [x["id"] for x in s["perPhase"]] != [p["id"] for p in d["phases"]]: errs.append("perPhase ids")
for x, p in zip(s["perPhase"], d["phases"]):
    pc = tally(p["tasks"])
    if x["total"] != len(p["tasks"]) or x["byState"] != pc or abs(x["percentDone"] - pct(pc, len(p["tasks"]))) > 0.05: errs.append("perPhase "+str(x["id"]))
md_tasks = re.findall(r"^\s*[-*] \[(.)\]\s+(P\d+\.\d+)\b", md, re.M)
by_id = {t["id"]: t for t in tasks}
missing = [i for _, i in md_tasks if i not in by_id]
if missing: errs.append(f"missing {missing}")
marks = {"x": "done", "X": "done", " ": "todo", "~": "in_progress", "-": "dropped"}
for m, i in md_tasks:
    if i in by_id and by_id[i]["state"] != marks.get(m, "unknown"): errs.append(i)
miss_p = [p for p in re.findall(r"^### (P\d+)\b", md, re.M) if p not in {p["id"] for p in d["phases"]}]
if miss_p: errs.append(f"phases {miss_p}")
dids = set(re.findall(r"^\|\s*(D\d+)\s*\|", md, re.M)) - {x["id"] for x in d["decisions"]}
if dids: errs.append(f"dec {dids}")
qids = set(re.findall(r"^\s*[-*] (Q\d+)\b", md, re.M)) - {x["id"] for x in d["openQuestions"]}
if qids: errs.append(f"q {qids}")
logs = {os.path.basename(f)[:-4] for f in glob.glob(root + "/progress/*.log")}
if set(d.get("progress", {})) != logs: errs.append("progress keys")
if errs:
    print("SELF-CHECK FAILED:", errs); sys.exit(1)
print(f"self-check ok: {len(tasks)} tasks {c} {s['percentDone']}%")
