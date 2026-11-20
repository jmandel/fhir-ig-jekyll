package org.hl7.fhir.jekyll.site;

import java.nio.file.Path;
import java.util.Locale;

/**
 * Drop-in for `jekyll build --destination X`, as the IG Publisher invokes it (`-jekyll <cmd>`, run
 * with the working directory set to temp/pages). Prints Jekyll's console lines; the publisher
 * keys on "Source:", "Liquid Exception:" and "done in" (PublisherGenerator#passJekyllFilter).
 *
 * usage: build [--source DIR] (--destination|-d) DIR [--renderer CLASS]
 */
public final class Main {
  private Main() {}

  static int renderThreads() {
    String v = System.getenv("SHIM_RENDER_THREADS");
    return v != null ? Integer.parseInt(v) : Math.max(1, Runtime.getRuntime().availableProcessors());
  }

  /** Renders pages in parallel (each thread has its own include-template cache); errors surface in page order. */
  static void renderAll(Site site, PageRenderer renderer) throws Exception {
    int threads = renderThreads();
    if (threads <= 1 || !(renderer instanceof JekyllRenderer)) {
      for (Site.Page p : site.pages) {
        renderOne(site, renderer, p);
      }
      return;
    }
    java.util.concurrent.ExecutorService ex = java.util.concurrent.Executors.newFixedThreadPool(threads, r -> {
      Thread t = new Thread(null, r, "render", 64L << 20); // deep Liquid/kramdown recursion headroom
      t.setDaemon(true);
      return t;
    });
    try {
      java.util.List<java.util.concurrent.Future<?>> fs = new java.util.ArrayList<>();
      for (Site.Page p : site.pages) {
        fs.add(ex.submit(() -> {
          renderOne(site, renderer, p);
          return null;
        }));
      }
      for (java.util.concurrent.Future<?> f : fs) {
        try {
          f.get();
        } catch (java.util.concurrent.ExecutionException e) {
          if (e.getCause() instanceof PageRenderer.RenderException) {
            throw (PageRenderer.RenderException) e.getCause();
          }
          throw e;
        }
      }
    } finally {
      ex.shutdownNow();
    }
  }

  private static void renderOne(Site site, PageRenderer renderer, Site.Page p) {
    java.util.List<String> w = new java.util.ArrayList<>();
    JekyllRenderer.CURRENT_PAGE_WARNINGS.set(w);
    try {
      p.output = renderer.render(site, p);
      site.writePage(p); // stream: write now, release the output string
    } catch (java.io.IOException e) {
      throw new java.io.UncheckedIOException(e);
    } finally {
      JekyllRenderer.CURRENT_PAGE_WARNINGS.remove();
      p.warnings = w;
    }
  }

  /**
   * Runs the build on a thread with a large stack, so deep Liquid/kramdown recursion never needs a
   * JVM flag (-Xss) from the caller; render threads get the same headroom (renderAll).
   */
  public static void main(String[] args) throws Exception {
    Throwable[] failure = new Throwable[1];
    Thread t = new Thread(null, () -> {
      try {
        run(args);
      } catch (Throwable e) {
        failure[0] = e;
      }
    }, "jekyll-shim", 512L << 20);
    t.start();
    t.join();
    if (failure[0] instanceof Exception) {
      throw (Exception) failure[0];
    }
    if (failure[0] != null) {
      throw new RuntimeException(failure[0]);
    }
  }

  static void run(String[] args) throws Exception {
    Path source = Path.of(".");
    Path dest = null;
    String rendererClass = "org.hl7.fhir.jekyll.site.JekyllRenderer";
    int i = 0;
    if (args.length > 0 && args[0].equals("build")) {
      i = 1;
    }
    for (; i < args.length; i++) {
      switch (args[i]) {
        case "--destination": case "-d": dest = Path.of(args[++i]); break;
        case "--source": case "-s": source = Path.of(args[++i]); break;
        case "--renderer": rendererClass = args[++i]; break;
        case "--quiet": case "-q": case "--trace": case "-t": break;
        default:
          System.err.println("unknown argument: " + args[i]);
          System.exit(2);
      }
    }
    if (dest == null) {
      dest = source.resolve("_site");
    }
    long t0 = System.nanoTime();
    Site site = new Site(source, dest);
    Path cfg = site.source.resolve("_config.yml");
    System.out.println("Configuration file: " + (cfg.toFile().isFile() ? cfg : "none"));
    System.out.println("            Source: " + site.source);
    System.out.println("       Destination: " + site.dest);
    System.out.println(" Incremental build: disabled. Enable with --incremental");
    System.out.println("      Generating... ");
    PageRenderer renderer = (PageRenderer) Class.forName(rendererClass).getDeclaredConstructor().newInstance();
    boolean timing = System.getenv("SHIM_TIMING") != null;
    try {
      long a = System.nanoTime();
      site.read();
      long b = System.nanoTime();
      renderer.prepare(site);
      site.prepareOutput();
      long c = System.nanoTime();
      Thread statics = new Thread(() -> {
        try {
          site.writeStatics();
        } catch (java.io.IOException ex) {
          throw new java.io.UncheckedIOException(ex);
        }
      }, "statics");
      java.util.concurrent.atomic.AtomicReference<Throwable> staticsFailure = new java.util.concurrent.atomic.AtomicReference<>();
      statics.setUncaughtExceptionHandler((th, ex) -> staticsFailure.set(ex));
      statics.start(); // static copies overlap with rendering
      renderAll(site, renderer);
      long d = System.nanoTime();
      for (Site.Page p : site.pages) {
        for (String w : p.warnings) {
          System.out.println("    Liquid Warning: " + w);
        }
      }
      statics.join();
      if (staticsFailure.get() != null) {
        throw new RuntimeException(staticsFailure.get());
      }
      long e = System.nanoTime();
      if (timing) {
        System.err.printf(Locale.ROOT, "SHIM_TIMING config=%.2fs read=%.2fs prepare=%.2fs render=%.2fs write=%.2fs pages=%d static=%d%n",
            (a - t0) / 1e9, (b - a) / 1e9, (c - b) / 1e9, (d - c) / 1e9, (e - d) / 1e9, site.pages.size(), site.staticFiles.size());
      }
    } catch (PageRenderer.RenderException e) {
      System.out.println("  Liquid Exception: " + e.getMessage());
      System.out.println("             Error: Run jekyll build --trace for more information.");
      System.exit(1);
    }
    double secs = (System.nanoTime() - t0) / 1e9;
    System.out.println(String.format(Locale.ROOT, "                    done in %.3f seconds.", secs));
    System.out.println(" Auto-regeneration: disabled. Use --watch to enable.");
  }
}
