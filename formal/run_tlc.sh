#!/usr/bin/env bash
# Model-check every configuration of formal/PhaseGuard.tla with TLC and
# write the outputs to results/formal/. Exit codes: TLC returns 0 when no
# error is found and 12 when an invariant is violated; the expected outcome
# of each configuration is asserted below, so this script fails if the
# guarded model ever acquires a violation or the unguarded model loses one.
#
# Usage: formal/run_tlc.sh [path/to/tla2tools.jar]
# The jar defaults to formal/tools/tla2tools.jar (gitignored). Download it
# from https://github.com/tlaplus/tlaplus/releases (asset tla2tools.jar).
set -u
cd "$(dirname "$0")"
JAR="${1:-tools/tla2tools.jar}"
OUT="../results/formal"
mkdir -p "$OUT"
[ -f "$JAR" ] || { echo "tla2tools.jar not found at $JAR" >&2; exit 2; }

run() {  # run <cfg> <output-file> <expected-exit>
    local cfg="$1" out="$2" expect="$3"
    java -XX:+UseParallelGC -jar "$JAR" -config "$cfg" -workers 1 -cleanup PhaseGuard.tla > "$OUT/$out" 2>&1
    local code=$?
    rm -f PhaseGuard_TTrace_*
    local summary
    summary=$(grep -E "^Error: Invariant|No error has been found" "$OUT/$out" | head -1)
    local states
    states=$(grep -E "states generated" "$OUT/$out")
    printf '%-38s exit=%-3s expected=%-3s %s\n    %s\n' "$cfg" "$code" "$expect" "$summary" "$states"
    [ "$code" -eq "$expect" ] || FAILED=1
}

FAILED=0
run PhaseGuard.cfg                     tlc-output.txt                     0
run PhaseGuard_bound8.cfg              tlc-output-bound8.txt              0
run PhaseGuard_bound4_coverage.cfg     tlc-output-bound4-coverage.txt     0
run PhaseGuard_bound8_witness.cfg      tlc-output-bound8-witness.txt      12
run PhaseGuard_unguarded.cfg           tlc-output-unguarded.txt           12
run PhaseGuard_unguarded_interrupt.cfg tlc-output-unguarded-interrupt.txt 12
run PhaseGuard_cancel_only.cfg         tlc-output-cancel-only.txt         12

# The counterexample the paper's Figure 1 describes, extracted from the run
# that isolates the interruption invariant.
sed -n '/^Error: Invariant/,/^Finished in/p' "$OUT/tlc-output-unguarded-interrupt.txt" > "$OUT/tlc-counterexample-unguarded.txt"

# Conformance: dump every reachable state of three configurations and
# compare each set exactly with a breadth-first search over the Python
# reference model (formal/conformance.py).
PY="${PYTHON:-python3}"
conform() {  # conform <cfg> <name> <bound> <conformance flags...>
    local cfg="$1" name="$2" bound="$3"; shift 3
    rm -f "$OUT/tlc-states-$name.dump"
    java -XX:+UseParallelGC -jar "$JAR" -config "$cfg" -workers 1 -cleanup -dump "$OUT/tlc-states-$name" PhaseGuard.tla > /dev/null 2>&1
    rm -f PhaseGuard_TTrace_*
    "$PY" conformance.py "$OUT/tlc-states-$name.dump" --bound "$bound" "$@" > "$OUT/conformance-$name.txt" 2>&1
    local code=$?
    printf 'conformance %-18s exit=%-3s %s\n' "$name" "$code" "$(tail -1 "$OUT/conformance-$name.txt")"
    [ "$code" -eq 0 ] || FAILED=1
}
conform PhaseGuard.cfg        bound4    4 --guard --cancel --preemptive
conform PhaseGuard_bound8.cfg bound8    8 --guard --cancel --preemptive
# The unguarded reproduction violates the invariants, so TLC is asked to dump
# with the invariants removed; PhaseGuard_unguarded_dump.cfg does that.
conform PhaseGuard_unguarded_dump.cfg unguarded 4 --no-guard --no-cancel --preemptive

if [ "$FAILED" -ne 0 ]; then echo "UNEXPECTED TLC OUTCOME" >&2; exit 1; fi
echo "all configurations produced their expected outcome"
