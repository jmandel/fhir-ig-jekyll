# Rewrites fragment(NAME, sdr.x(...)/tr.x(), f.getOutputNames(), r, vars, null, ...) calls in
# generateOutputsStructureDefinition to lazyFragment(NAME, () -> EXPR, ...). Prints what it converted.
import re, sys
path = sys.argv[1]
src = open(path).read()
a = src.index("  private void generateOutputsStructureDefinition(")
b = src.index("  private boolean wantGen(", a)
body = src[a:b]
EAGER = {"obligations", "obligationsForActor", "expansion", "otherVersions", "compareImposes"}
MARK = ", f.getOutputNames(), r, vars, null, "
out, n = [], 0
for line in body.split("\n"):
    s = line.lstrip()
    if s.startswith("fragment(") and MARK in s:
        head, tail = s[len("fragment("):].split(MARK, 1)
        name, expr = head.split(", ", 1)
        m = re.match(r"(sdr|tr)\.(\w+)\(", expr)
        if m and m.group(2) not in EAGER:
            line = line[:len(line)-len(s)] + "lazyFragment(" + name + ", () -> " + expr + MARK + tail
            n += 1
            print("lazy:", m.group(2), name.split("+")[-1])
    out.append(line)
src = src[:a] + "\n".join(out) + src[b:]
open(path, "w").write(src)
print("converted", n)
