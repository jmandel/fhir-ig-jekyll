package org.hl7.fhir.igtools.publisher;

import java.io.IOException;
import java.io.PrintWriter;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import org.hl7.fhir.model.BaseList;
import org.hl7.fhir.model.core.CanonicalType;
import org.hl7.fhir.model.core.ElementDefinition;
import org.hl7.fhir.model.core.ElementDefinition.TypeRefComponent;
import org.hl7.fhir.model.core.StructureDefinition;

/**
 * -Dig.fragments.verify=true (with -jekyll-engine shim-lazy): checks that deferral doesn't change any fragment.
 * <ul>
 * <li>Every deferred fragment is also rendered at its eager point (so the build's state evolves exactly as
 * in an eager build); after Jekyll, each one is compared with what its deferred generator gives (generating
 * the ones no page asked for). Mismatches are listed with the first differing offset.</li>
 * <li>Each StructureDefinition with deferred views is fingerprinted when its views are registered and at
 * every phase boundary after that (after each file's html, end of html, spreadsheets, summaries, before and
 * after Jekyll); changes are reported with the phase in which they were first seen.</li>
 * <li>The type lists of those SDs' elements are replaced by a list that records the first change made to
 * it (with the phase and the stack), so the code that changes them can be named.</li>
 * </ul>
 * Report: temp/fragment-verify.txt. Diagnostic only: it makes the build slower and uses more memory.
 */
class FragmentVerifier {

  static volatile String phase = "start";

  private static final class Expected {
    final String code;
    final String content;

    Expected(String code, String content) {
      this.code = code;
      this.content = content;
    }
  }

  private final Map<String, Expected> expected = new LinkedHashMap<>();
  private final Map<StructureDefinition, String> sdNames = new IdentityHashMap<>();
  private final Map<StructureDefinition, List<String>> fingerprints = new IdentityHashMap<>();
  private final List<String> stateLog = Collections.synchronizedList(new ArrayList<>());
  private final List<String> trapLog = Collections.synchronizedList(new ArrayList<>());
  private static Field typeListField;

  void expect(String includeName, String code, String content) {
    expected.put(includeName, new Expected(code, content));
  }

  // ---- SD state ---------------------------------------------------------------------------------

  void watch(String name, StructureDefinition sd) {
    if (sdNames.containsKey(sd)) {
      return;
    }
    sdNames.put(sd, name);
    fingerprints.put(sd, fingerprint(sd));
    if (sd.hasSnapshot()) {
      for (ElementDefinition ed : sd.getSnapshot().getElementList()) {
        trap(name, "snapshot", ed);
      }
    }
    if (sd.hasDifferential()) {
      for (ElementDefinition ed : sd.getDifferential().getElementList()) {
        trap(name, "differential", ed);
      }
    }
  }

  void checkpoint(String label) {
    for (Map.Entry<StructureDefinition, List<String>> e : fingerprints.entrySet()) {
      List<String> now = fingerprint(e.getKey());
      List<String> before = e.getValue();
      if (!now.equals(before)) {
        StringBuilder b = new StringBuilder();
        b.append("STATE ").append(sdNames.get(e.getKey())).append(" changed by: ").append(label).append('\n');
        int shown = 0;
        int n = Math.max(now.size(), before.size());
        for (int i = 0; i < n && shown < 8; i++) {
          String x = i < before.size() ? before.get(i) : "(none)";
          String y = i < now.size() ? now.get(i) : "(none)";
          if (!x.equals(y)) {
            b.append("    - ").append(x).append('\n').append("    + ").append(y).append('\n');
            shown++;
          }
        }
        stateLog.add(b.toString());
        e.setValue(now);
      }
    }
  }

  private static List<String> fingerprint(StructureDefinition sd) {
    List<String> res = new ArrayList<>();
    res.add("snapshot=" + (sd.hasSnapshot() ? sd.getSnapshot().getElementList().size() : -1)
        + " differential=" + (sd.hasDifferential() ? sd.getDifferential().getElementList().size() : -1));
    if (sd.hasSnapshot()) {
      for (ElementDefinition ed : sd.getSnapshot().getElementList()) {
        res.add("S " + fingerprint(ed));
      }
    }
    if (sd.hasDifferential()) {
      for (ElementDefinition ed : sd.getDifferential().getElementList()) {
        res.add("D " + fingerprint(ed));
      }
    }
    return res;
  }

  private static String fingerprint(ElementDefinition ed) {
    StringBuilder b = new StringBuilder();
    b.append(ed.getId()).append(" path=").append(ed.getPath());
    if (ed.hasContentReference()) {
      b.append(" cr=").append(ed.getContentReference());
    }
    b.append(" min=").append(ed.hasMin() ? ed.getMin() : "-").append(" max=").append(ed.hasMax() ? ed.getMax() : "-");
    b.append(" types[").append(ed.getTypeList().size()).append("]=");
    for (TypeRefComponent t : ed.getTypeList()) {
      b.append(t.isEmpty() ? "(empty)" : t.getCode());
      for (CanonicalType p : t.getProfileList()) {
        b.append(" p:").append(p.getValue());
      }
      for (CanonicalType p : t.getTargetProfileList()) {
        b.append(" t:").append(p.getValue());
      }
      b.append(';');
    }
    b.append(" ext=").append(ed.getExtension().size());
    return b.toString();
  }

  // ---- type list trap ---------------------------------------------------------------------------

  /** A type list that records the first change made to it. */
  private final class TrapList extends BaseList<TypeRefComponent> {
    private static final long serialVersionUID = 1L;
    private final String where;
    private boolean armed = true;

    TrapList(ElementDefinition owner, String where, List<TypeRefComponent> current) {
      super(owner, current);
      this.where = where;
    }

    private void hit(String op) {
      if (armed) {
        armed = false;
        StringBuilder b = new StringBuilder("TRAP ").append(where).append(": typeList.").append(op)
            .append(" during: ").append(phase).append('\n');
        StackTraceElement[] st = new Throwable().getStackTrace();
        for (int i = 2; i < Math.min(st.length, 30); i++) {
          b.append("      at ").append(st[i]).append('\n');
        }
        trapLog.add(b.toString());
      }
    }

    @Override
    public void add(int index, TypeRefComponent element) {
      hit("add");
      super.add(index, element);
    }

    @Override
    public TypeRefComponent set(int index, TypeRefComponent element) {
      hit("set");
      return super.set(index, element);
    }

    @Override
    public TypeRefComponent remove(int index) {
      hit("remove");
      return super.remove(index);
    }
  }

  private void trap(String sdName, String list, ElementDefinition ed) {
    try {
      if (typeListField == null) {
        typeListField = ElementDefinition.class.getDeclaredField("typeList");
        typeListField.setAccessible(true);
      }
      typeListField.set(ed, new TrapList(ed, sdName + " " + list + " " + ed.getId(), new ArrayList<>(ed.getTypeList())));
    } catch (ReflectiveOperationException e) {
      trapLog.add("TRAP not installed: " + e);
    }
  }

  // ---- comparison -------------------------------------------------------------------------------

  /** After Jekyll: compares each deferred fragment with its eager rendering; writes the report. Returns the summary line. */
  String finish(FragmentRegistry registry, Path report) throws IOException {
    int same = 0;
    int differ = 0;
    int failed = 0;
    Map<String, int[]> byCode = new TreeMap<>();
    List<String> details = new ArrayList<>();
    for (Map.Entry<String, Expected> e : expected.entrySet()) {
      String actual;
      try {
        actual = registry.contentOf(e.getKey());
      } catch (Exception ex) {
        actual = null;
      }
      String exp = FragmentRegistry.asWrittenToFile(e.getValue().content);
      int[] c = byCode.computeIfAbsent(e.getValue().code, k -> new int[2]);
      if (actual == null) {
        failed++;
        details.add("FAILED " + e.getKey());
      } else if (actual.equals(exp)) {
        same++;
        c[0]++;
      } else {
        differ++;
        c[1]++;
        int i = 0;
        while (i < actual.length() && i < exp.length() && actual.charAt(i) == exp.charAt(i)) {
          i++;
        }
        details.add("DIFFER " + e.getKey() + " (" + e.getValue().code + ") at " + i + " of " + exp.length() + "/" + actual.length()
            + "\n    eager: " + snippet(exp, i) + "\n    lazy:  " + snippet(actual, i));
      }
    }
    String summary = "Fragment verification: " + expected.size() + " deferred fragments, " + same + " identical, " + differ
        + " differ, " + failed + " failed; " + stateLog.size() + " SD state changes after registration, " + trapLog.size()
        + " type-list changes trapped (" + report + ")";
    try (PrintWriter w = new PrintWriter(Files.newBufferedWriter(report, StandardCharsets.UTF_8))) {
      w.println(summary);
      w.println();
      w.println("== by fragment code: identical / differ");
      for (Map.Entry<String, int[]> e : byCode.entrySet()) {
        w.println(e.getKey() + "\t" + e.getValue()[0] + "\t" + e.getValue()[1]);
      }
      w.println();
      w.println("== differing fragments");
      details.forEach(w::println);
      w.println();
      w.println("== SD state changes after the views were registered");
      stateLog.forEach(w::print);
      w.println();
      w.println("== type-list changes (first per element)");
      trapLog.forEach(w::print);
    }
    return summary;
  }

  private static String snippet(String s, int at) {
    int a = Math.max(0, at - 100);
    int b = Math.min(s.length(), at + 120);
    return s.substring(a, b).replace('\n', ' ').replace('\r', ' ');
  }
}
