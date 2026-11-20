package org.hl7.fhir.igtools.publisher;

import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;

import org.hl7.fhir.model.core.ElementDefinition;
import org.hl7.fhir.model.core.ElementDefinition.TypeRefComponent;
import org.hl7.fhir.model.core.StructureDefinition;

/**
 * -jekyll-engine shim-lazy: the StructureDefinition as the views rendered before its -expansion fragment saw it.
 *
 * Rendering the expansion (PEBuilder) calls ElementDefinition.getTypeFirstRep() on elements that have no type
 * (contentReference elements such as Observation.component.referenceRange), and that getter adds an empty type.
 * The views generated before the expansion (snapshot, by-key, ...) show such elements with the Element icon,
 * the ones after it with the Resource icon. Deferred views run after the expansion, so for the ones that
 * eagerly run before it, this hides the empty types the expansion added while they render (generation is
 * serialized and the publisher's thread waits in Jekyll, so nothing else sees the change), and puts them back.
 */
class PreExpansionView {

  /** Content of a deferred fragment. */
  interface Source {
    String get() throws Exception;
  }

  private final List<ElementDefinition> untyped = new ArrayList<>();

  /** Call before anything renders sd: remembers the elements that have no type yet. */
  PreExpansionView(StructureDefinition sd) {
    // (has* first: getSnapshot()/getDifferential() would create the component)
    if (sd.hasSnapshot()) {
      collect(sd.getSnapshot().getElementList());
    }
    if (sd.hasDifferential()) {
      collect(sd.getDifferential().getElementList());
    }
  }

  private void collect(List<ElementDefinition> elements) {
    for (ElementDefinition ed : elements) {
      if (!ed.hasType() && ed.getTypeList().isEmpty()) {
        untyped.add(ed);
      }
    }
  }

  String render(Source source) throws Exception {
    Map<ElementDefinition, List<TypeRefComponent>> hidden = new IdentityHashMap<>();
    for (ElementDefinition ed : untyped) {
      if (!ed.getTypeList().isEmpty() && allEmpty(ed.getTypeList())) {
        hidden.put(ed, new ArrayList<>(ed.getTypeList()));
        ed.getTypeList().clear();
      }
    }
    try {
      return source.get();
    } finally {
      for (Map.Entry<ElementDefinition, List<TypeRefComponent>> e : hidden.entrySet()) {
        e.getKey().getTypeList().addAll(e.getValue());
      }
    }
  }

  private static boolean allEmpty(List<TypeRefComponent> types) {
    for (TypeRefComponent t : types) {
      if (!t.isEmpty()) {
        return false;
      }
    }
    return true;
  }
}
