// Launcher used by scripts/determinism.sh to perturb identity hash codes without changing the program.
//
// HotSpot's default identity hash (-XX:hashCode=5) is a per-thread xor-shift generator. Its state is
// seeded at thread creation from os::random(), whose seed is fixed, so the main thread of two runs
// usually sees the *same* sequence of identity hash codes and HashSet/HashMap iteration over objects
// without hashCode() is often stable run to run. Drawing K identity hashes on the main thread before
// handing over shifts that sequence by K: every later Object.hashCode() on the main thread differs,
// reproducibly for a given K. Usage:
//   java -Dhashshift=K -cp <jar>:<dir with HashShift.class> HashShift <main class> [args...]
public final class HashShift {
  public static void main(String[] a) throws Throwable {
    int k = Integer.getInteger("hashshift", 0);
    long sink = 0;
    for (int i = 0; i < k; i++) sink += System.identityHashCode(new Object());
    if (sink == 42) System.err.print("");
    String[] rest = java.util.Arrays.copyOfRange(a, 1, a.length);
    try {
      Class.forName(a[0]).getMethod("main", String[].class).invoke(null, (Object) rest);
    } catch (java.lang.reflect.InvocationTargetException e) {
      throw e.getCause();
    }
  }
}
