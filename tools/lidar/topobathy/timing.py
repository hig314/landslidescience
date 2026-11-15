"""Sub-step timing for the chain's stages (2026-11-14, review_1114: the logs carry no times, so a slow box cannot say where it was slow).

    import topobathy.timing as timing; timing.install("fill")        # at the top of a stage's entry point

Does nothing unless $TOPO_TIMING_DIR is set (topobathy_chain.sh / run_box.sh set it to the box's work dir).  With it, every printed line
that arrives more than MIN_GAP_S after the one before is recorded in <dir>/timing_<stage>.log as
    <seconds since start>  +<seconds this step took>  <the line that ENDED the step>
The stage's own log is untouched (scripts grep it)."""
import atexit, builtins, os, time

MIN_GAP_S = 5.0


def install(stage):
    d = os.environ.get("TOPO_TIMING_DIR")
    if not d or getattr(builtins, "_topo_timing", False):
        return
    try:
        f = open(os.path.join(d, f"timing_{stage}.log"), "w")
    except OSError:
        return
    builtins._topo_timing = True
    t0 = time.time(); last = [t0]; bp = builtins.print
    f.write(f"# {stage} started {time.strftime('%Y-%m-%d %H:%M:%S')}; lines arriving > {MIN_GAP_S:g} s after the one before\n"); f.flush()

    def tprint(*a, **k):
        bp(*a, **k)
        now = time.time()
        if now - last[0] >= MIN_GAP_S and k.get("file") is None:
            f.write(f"{now - t0:8.0f}s  +{now - last[0]:6.0f}s  {' '.join(str(x) for x in a)[:140]}\n"); f.flush()
        last[0] = now

    builtins.print = tprint
    atexit.register(lambda: (f.write(f"{time.time() - t0:8.0f}s  end\n"), f.close()))
