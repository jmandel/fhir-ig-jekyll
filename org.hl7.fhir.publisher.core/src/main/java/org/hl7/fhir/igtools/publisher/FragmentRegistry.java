package org.hl7.fhir.igtools.publisher;

import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

import org.hl7.fhir.jekyll.IncludeProvider;

/**
 * The fragments of a build that runs Jekyll in process (-jekyll-engine shim-lazy): instead of files in
 * temp/pages/_includes, fragments are held here and handed to the shim when a page first includes them.
 * <ul>
 * <li>{@link #put}: content already generated (most fragment() call sites)</li>
 * <li>{@link #putLazy}: a generator, run only if a page includes the fragment (the expensive families)</li>
 * <li>{@link #alias}: a second name for an entry (the unsuffixed copy of a default-language fragment)</li>
 * </ul>
 * The shim asks for each name at most once, from its render threads; generators touch publisher state
 * that isn't thread-safe, so they run one at a time (the publisher's own thread is waiting in
 * Shim.build meanwhile). A name the registry doesn't have is looked up on disk by the shim, as Jekyll
 * would (template includes, hand-written includes, fragments written to disk because the memory budget
 * ran out).
 */
public class FragmentRegistry implements IncludeProvider {

  /** Produces the include content (already wrapped by wrapLiquid), or null when generation failed. */
  @FunctionalInterface
  public interface Generator {
    String generate() throws Exception;
  }

  private static final class Entry {
    final Generator generator;
    String content;
    boolean done;

    Entry(String content) {
      this.generator = null;
      this.content = content;
      this.done = true;
    }

    Entry(Generator generator) {
      this.generator = generator;
    }
  }

  private final Map<String, Entry> entries = new ConcurrentHashMap<>();
  private final Object generationLock = new Object();
  private final long maxEagerBytes;
  private final AtomicLong eagerBytes = new AtomicLong();
  private final AtomicInteger eager = new AtomicInteger();
  private final AtomicInteger lazy = new AtomicInteger();
  private final AtomicInteger aliases = new AtomicInteger();
  private final AtomicInteger delivered = new AtomicInteger();
  private final AtomicInteger generated = new AtomicInteger();
  private final AtomicInteger failed = new AtomicInteger();
  private final AtomicLong generationNanos = new AtomicLong();
  private final AtomicLong deliveredBytes = new AtomicLong();

  public FragmentRegistry(long maxEagerBytes) {
    this.maxEagerBytes = maxEagerBytes;
  }

  /** False when holding this much more generated content would exceed the memory budget (write the file instead). */
  public boolean hasRoomFor(long chars) {
    return eagerBytes.get() + chars <= maxEagerBytes;
  }

  /**
   * The content as it reads back from the file fragment() would have written: FileUtilities.stringToBytes
   * encodes with String.getBytes(UTF_8), which turns unpaired surrogates (seen in rendered content)
   * into '?'. Without this the shim's UTF-8 page writer would fail on them.
   */
  static String asWrittenToFile(String s) {
    if (s == null) {
      return null;
    }
    for (int i = 0; i < s.length(); i++) {
      if (Character.isSurrogate(s.charAt(i))) {
        return new String(s.getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8);
      }
    }
    return s;
  }

  public void put(String name, String wrappedContent) {
    wrappedContent = asWrittenToFile(wrappedContent);
    eager.incrementAndGet();
    eagerBytes.addAndGet(wrappedContent.length());
    entries.put(name, new Entry(wrappedContent));
  }

  public void putLazy(String name, Generator generator) {
    lazy.incrementAndGet();
    entries.put(name, new Entry(generator));
  }

  /** name serves the same content as target (generated once). No-op when target isn't registered. */
  public void alias(String name, String target) {
    Entry e = entries.get(target);
    if (e != null) {
      aliases.incrementAndGet();
      entries.put(name, e);
    }
  }

  @Override
  public String include(String name) throws Exception {
    Entry e = entries.remove(name); // asked once: the shim keeps what it gets
    if (e == null) {
      return null;
    }
    String content;
    if (e.generator == null) {
      content = e.content;
    } else {
      synchronized (generationLock) {
        if (!e.done) {
          long t0 = System.nanoTime();
          try {
            e.content = asWrittenToFile(e.generator.generate());
          } finally {
            e.done = true;
            generationNanos.addAndGet(System.nanoTime() - t0);
          }
          generated.incrementAndGet();
          if (e.content == null) {
            failed.incrementAndGet();
          }
        }
        content = e.content;
      }
    }
    if (content != null) {
      delivered.incrementAndGet();
      deliveredBytes.addAndGet(content.length());
    }
    return content;
  }

  /** Diagnostic (-Dig.fragments.drain): generate every pending deferred fragment now; they stay registered. */
  public void drain() throws Exception {
    for (Entry e : entries.values()) {
      if (e.generator != null) {
        synchronized (generationLock) {
          if (!e.done) {
            e.content = asWrittenToFile(e.generator.generate());
            e.done = true;
          }
        }
      }
    }
  }

  /** One line for the log, after the build: what was registered, generated, and never needed. */
  public String summary() {
    int notGenerated = lazy.get() - generated.get();
    return "Fragments in memory: " + eager.get() + " generated during the build (" + (eagerBytes.get() / 1024) + " KB), "
        + lazy.get() + " deferred, " + aliases.get() + " aliases; Jekyll included " + delivered.get() + " ("
        + (deliveredBytes.get() / 1024) + " KB); deferred fragments generated: " + generated.get()
        + (failed.get() > 0 ? " (" + failed.get() + " failed)" : "") + " in "
        + (generationNanos.get() / 1000000) + " ms, skipped: " + notGenerated;
  }

  public int getLazyCount() {
    return lazy.get();
  }

  public int getGeneratedCount() {
    return generated.get();
  }

  public int getEagerCount() {
    return eager.get();
  }

  public int getDeliveredCount() {
    return delivered.get();
  }

  /** Releases whatever no page included. */
  public void clear() {
    entries.clear();
  }
}
