"""Translate TRACKER.md + progress/*.log into tracker-app/tracker.json.tmp (schema: schema.md).
usage: python3 -I gen.py <trigger>   (the project root is this file's parent directory, or $TRACKER_ROOT)
"""
import json, re, sys, os, glob, hashlib, subprocess
root=os.environ.get("TRACKER_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
trigger=sys.argv[1]
def sh(c): return subprocess.check_output(c,shell=True,cwd=root,text=True,executable="/bin/bash").strip()
gen=sh("date -u +%Y-%m-%dT%H:%M:%SZ")
smt=sh('date -u -r "$(stat -L -f %m TRACKER.md)" +%Y-%m-%dT%H:%M:%SZ')
raw=open(root+"/TRACKER.md","rb").read()
sha=hashlib.sha256(raw).hexdigest()
text=raw.decode("utf-8")
lines=text.split("\n")
warns=[]
def warn(section,msg,i=None,rawl=None):
    w={"section":section,"message":msg}
    if i is not None: w["line"]=i
    if rawl is not None: w["raw"]=rawl.strip()[:200]
    warns.append(w)
# links
def links(s, own=None):
    out=[];seen=set()
    def add(k,t,l=None):
        if (k,t) in seen: return
        seen.add((k,t)); d={"kind":k,"target":t}
        if l is not None: d["label"]=l
        out.append(d)
    items=[]  # (pos, kind, target, label)
    masked=list(s)
    for m in re.finditer(r"\[([^\]]+)\]\(([^)\s]+)\)",s):
        t=m.group(2)
        if t.startswith("#"): items.append((m.start(),"anchor",t,m.group(1)))
        elif re.match(r"https?://",t): items.append((m.start(),"url",t,m.group(1)))
        for i in range(m.start(),m.end()): masked[i]=" "
    for m in re.finditer(r"https?://[^\s)\]>]+",s):
        if not any(masked[i]==" " for i in range(m.start(),m.end())):
            items.append((m.start(),"url",m.group(0).rstrip(".,;"),None))
            for i in range(m.start(),m.end()): masked[i]=" "
    ms="".join(masked)
    for m in re.finditer(r"`([^`]+)`",ms):
        c=m.group(1)
        if ("/" in c or "." in c) and " " not in c and "{{" not in c:
            items.append((m.start(),"path",c,None))
        for i in range(m.start(),m.end()): masked[i]=" "
    ms2="".join(masked)
    for m in re.finditer(r"\b[PDQ]\d+(?:\.\d+)?\b",ms2):
        if m.group(0)!=own: items.append((m.start(),"ref",m.group(0),None))
    for m in re.finditer(r"\bsee (Running|Notes log|Decisions|Open questions|Key facts)\b",ms2):
        items.append((m.start(),"section",m.group(1),None))
    items.sort(key=lambda x:x[0])
    for _,k,t,l in items: add(k,t,l)
    return out
# sections
secs=[];cur=None
title=None
for i,l in enumerate(lines,1):
    if l.startswith("# ") and title is None: title=l[2:].strip()
    elif l.startswith("## "):
        cur={"heading":l[3:].strip(),"start":i,"lines":[]}; secs.append(cur)
    elif cur is not None: cur["lines"].append((i,l))
def find(name):
    for s in secs:
        if s["heading"].lower().startswith(name): return s
    return None
def body(s):
    b=[l for _,l in s["lines"]]
    while b and not b[0].strip(): b.pop(0)
    while b and not b[-1].strip(): b.pop()
    return "\n".join(b)
known=["how we work","goal","plan","decisions","running","open questions","key facts","notes log","layout"]
extra=[]
for s in secs:
    h=s["heading"].lower()
    if not any(h.startswith(k) for k in known):
        extra.append({"heading":s["heading"],"markdown":body(s)})
        warn(s["heading"],"unknown section; copied into extraSections",s["start"],"## "+s["heading"])
goal=None
s=find("goal")
if s: goal=body(s)
hw=[]
s=find("how we work")
if s:
    for _,l in s["lines"]:
        m=re.match(r"^\s*[-*]\s+(.*)$",l)
        if m: hw.append(m.group(1))
# plan
plan=find("plan")
phases=[];ph=None;seen_ids={}
if plan is None: print("NOPLAN"); sys.exit(3)
lastitem=None
for i,l in plan["lines"]:
    m=re.match(r"^###\s+(.*)$",l)
    if m:
        h=m.group(1).strip()
        mm=re.match(r"^(P\d+):\s*(.*)$",h)
        if not mm:
            mm2=re.match(r"^(P\d+)\s*[-–—]?\s+(.*)$",h)
            if mm2: warn("Plan","phase heading without colon",i,l); mm=mm2
        if mm: pid,t=mm.group(1),mm.group(2)
        else: pid,t=None,h; warn("Plan","phase heading without Pn",i,l)
        ph={"id":pid,"title":t,"tasks":[],"_desc":[]}
        am=re.search(r"acceptance\s*[=:]\s*(.*)$",t)
        if am: ph["acceptance"]=am.group(1).strip()
        if re.search(r"delegated",t,re.I): ph["delegated"]=True
        phases.append(ph); lastitem=None; continue
    m=re.match(r"^(\s*)[-*]\s+\[(.)\]\s+(.*)$",l)
    if m:
        if ph is None:
            ph={"id":None,"title":"(unphased)","tasks":[],"_desc":[]}; phases.append(ph)
            warn("Plan","task before first phase heading; placed in (unphased)",i,l)
        marker,rest=m.group(2),m.group(3)
        st={" ":"todo","~":"in_progress","x":"done","X":"done","-":"dropped"}.get(marker,"unknown")
        im=re.match(r"^(P\d+\.\d+)(?![\w.])\s*(.*)$",rest)
        if im: tid,tx=im.group(1),im.group(2)
        else:
            tid,tx=None,rest; warn("Plan","checkbox task without Pn.k ID",i,l)
        if tid:
            if ph["id"] and not tid.startswith(ph["id"]+"."): warn("Plan","task ID prefix does not match phase "+ph["id"],i,l)
            if tid in seen_ids: warn("Plan","duplicate task ID "+tid,i,l)
            seen_ids[tid]=1
        t={"id":tid,"state":st,"text":tx}
        if st=="unknown":
            t["marker"]=marker; warn("Plan","unknown checkbox marker ["+marker+"]",i,l)
        ph["tasks"].append(t); lastitem=t; continue
    m=re.match(r"^\s+[-*]\s+(.*)$",l)
    if m and lastitem is not None:
        lastitem.setdefault("notes",[]).append(m.group(1)); continue
    if l.strip()=="" : continue
    if re.match(r"^\s+\S",l) and lastitem is not None:
        lastitem["text"]+=" "+l.strip(); continue
    m=re.match(r"^[-*]\s+(.*)$",l)
    if m and ph is not None:
        ph["_desc"].append(l.strip()); warn("Plan","bullet without checkbox",i,l); continue
    if ph is not None and not l.startswith("#"):
        ph["_desc"].append(l.strip())
for p in phases:
    d=p.pop("_desc")
    if d: p["description"]="\n".join(d)
    for t in p["tasks"]:
        tid=t["id"]
        if "notes" in t:
            pass
        lk=links(t["text"]+(" "+" ".join(t.get("notes",[])) if t.get("notes") else ""),tid)
        if lk: t["links"]=lk
        if t["state"]=="dropped":
            m=re.search(r"\*\*\[(?:deferred|dropped)[^:\]]*:\s*(.*?)\]\*\*",t["text"])
            if m: t["droppedReason"]=m.group(1)
    sts=[t["state"] for t in p["tasks"]]
    if sts and all(s in("done","dropped") for s in sts): p["status"]="done"
    elif any(s in("in_progress","done") for s in sts): p["status"]="in_progress"
    else: p["status"]="todo"
# order keys
def ordphase(p):
    o={"id":p["id"],"title":p["title"]}
    for k in("description",): 
        if k in p:o[k]=p[k]
    o["status"]=p["status"]; o["tasks"]=[]
    for t in p["tasks"]:
        tt={"id":t["id"],"state":t["state"],"text":t["text"]}
        for k in("marker","notes","droppedReason","links"):
            if k in t: tt[k]=t[k]
        o["tasks"].append(tt)
    for k in("acceptance","delegated"):
        if k in p:o[k]=p[k]
    return o
phases=[ordphase(p) for p in phases]
# tables
def table(sec,secname):
    rows=[];hdr=None;prose=[]
    if not sec: return None,[],[]
    for i,l in sec["lines"]:
        if l.strip().startswith("|"):
            cells=[c.strip() for c in l.strip().strip("|").split("|")]
            if all(re.match(r"^:?-+:?$",c) for c in cells): continue
            if hdr is None: hdr=cells; continue
            rows.append((i,l,cells))
        elif l.strip():
            prose.append(l.strip())
    return hdr,rows,prose
hdr,rows,_=table(find("decisions"),"Decisions")
decisions=[]
for i,l,c in rows:
    n=len(hdr)
    if len(c)>n:
        c=c[:n-2]+[" | ".join(c[n-2:-1])]+[c[-1]]; warn("Decisions","row has extra cells; joined into Decision text",i,l)
    elif len(c)<n:
        c=c+[None]*(n-len(c)); warn("Decisions","row has fewer cells than header",i,l)
    mp=dict(zip([h.lower() for h in hdr],c))
    did=mp.get("#"); 
    if not did or not re.match(r"^D\d+$",did): warn("Decisions","row without Dn ID",i,l); did=None
    d={"id":did,"text":mp.get("decision"),"date":mp.get("date")}
    lk=links(d["text"] or "",did)
    if lk: d["links"]=lk
    decisions.append(d)
hdr,rows,prose=table(find("running"),"Running")
running=[]
for i,l,c in rows:
    n=len(hdr); hl=[h.lower() for h in hdr]
    if len(c)>n:
        c=c[:n-1]+[" | ".join(c[n-1:])]; warn("Running","row has extra cells",i,l)
    elif len(c)<n:
        c=c+[None]*(n-len(c)); warn("Running","row has fewer cells than header",i,l)
    mp=dict(zip(hl,c))
    r={"what":mp.get("what"),"command":mp.get("command"),"output":mp.get("output"),"status":mp.get("status")}
    m=re.search(r"bg task `([^`]+)`",r["command"] or "")
    if m: r["taskRef"]=m.group(1)
    running.append(r)
runningIntro="\n".join(prose) if prose else None
oq=[]
s=find("open questions")
if s:
    for i,l in s["lines"]:
        m=re.match(r"^\s*[-*]\s+(?:~~)?(Q\d+)[.:]?\s*(.*)$",l)
        if m:
            q={"id":m.group(1),"text":m.group(2)}
            if re.match(r"^\s*[-*]\s+~~",l) or re.match(r"(?i)^(resolved|answered)",m.group(2)): q["resolved"]=True
            lk=links(q["text"],q["id"])
            if lk:q["links"]=lk
            oq.append(q)
        elif re.match(r"^\s*[-*]\s+",l):
            t=re.sub(r"^\s*[-*]\s+","",l); oq.append({"id":None,"text":t}); warn("Open questions","bullet without Qn ID",i,l)
kf=[]
s=find("key facts")
if s:
    for i,l in s["lines"]:
        m=re.match(r"^\s*[-*]\s+(.*)$",l)
        if m:
            f={"text":m.group(1)}
            lk=links(f["text"])
            if lk:f["links"]=lk
            kf.append(f)
notes=[]
s=find("notes log")
if s:
    seq=0
    for i,l in s["lines"]:
        m=re.match(r"^\s*[-*]\s+(.*)$",l)
        if not m: continue
        seq+=1; b=m.group(1)
        dm=re.match(r"^\*\*(\d{4}-\d\d-\d\d)[^*]*?:?\*\*\s*(.*)$",b) or re.match(r"^(\d{4}-\d\d-\d\d):?\s+(.*)$",b)
        if dm: n={"date":dm.group(1),"text":dm.group(2),"seq":seq}
        else: n={"date":None,"text":b,"seq":seq}; warn("Notes log","note without date",i,l)
        lk=links(n["text"])
        if lk:n["links"]=lk
        notes.append(n)
    notes.sort(key=lambda n:(n["date"] is not None, n["date"] or "", n["seq"]),reverse=True)
layout=None
s=find("layout")
if s:
    b=body(s).split("\n")
    if b and b[0].startswith("```"): b=b[1:]
    if b and b[-1].startswith("```"): b=b[:-1]
    layout="\n".join(b)
# progress
progress={}
for f in sorted(glob.glob(root+"/progress/*.log")):
    name=os.path.basename(f)[:-4]
    rel="progress/"+name+".log"
    mt=sh('date -u -r "$(stat -L -f %m '+rel+')" +%Y-%m-%dT%H:%M:%SZ')
    nb=[l for l in open(f,encoding="utf-8").read().split("\n") if l.strip()]
    ents=[]
    for l in reversed(nb[-5:]):
        parts=[p.strip() for p in l.split("|",2)]
        if len(parts)==3 and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d",parts[0]) and parts[1] in("started","milestone","blocked","done"):
            ents.append({"timestamp":parts[0],"status":parts[1],"text":parts[2]})
        else:
            ents.append({"timestamp":None,"status":"unknown","text":l}); warn(rel,"unparseable progress line",None,l)
    progress[name]={"file":rel,"mtime":mt,"lineCount":len(nb),"entries":ents}
# summary
states=["todo","in_progress","done","dropped","unknown"]
def tally(ts):
    c=dict.fromkeys(states,0)
    for t in ts: c[t["state"]]+=1
    return c
def pct(c,n):
    den=n-c["dropped"]; return round(100*c["done"]/den,1) if den else 0.0
alltasks=[t for p in phases for t in p["tasks"]]
c=tally(alltasks)
summary={"total":len(alltasks),"byState":c,"percentDone":pct(c,len(alltasks)),
 "perPhase":[{"id":p["id"],"total":len(p["tasks"]),"byState":tally(p["tasks"]),"percentDone":pct(tally(p["tasks"]),len(p["tasks"]))} for p in phases],
 "inProgress":[t["id"] for t in alltasks if t["state"]=="in_progress"]}
out={"schemaVersion":1,"generatedAt":gen,"sourceMtime":smt,"source":{"path":root+"/TRACKER.md","sha256":sha},
 "title":title,"goal":goal,"howWeWork":hw,"summary":summary,"phases":phases,"decisions":decisions,
 "runningIntro":runningIntro,"running":running,"openQuestions":oq,"keyFacts":kf,"notes":notes,"layout":layout,
 "extraSections":extra,"progress":progress,"parseWarnings":warns}
json.dump(out,open(root+"/tracker-app/tracker.json.tmp","w",encoding="utf-8"),ensure_ascii=False,indent=2)
open(root+"/tracker-app/tracker.json.tmp","a").write("\n")
