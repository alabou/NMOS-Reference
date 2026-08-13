#!/usr/bin/env bash
#
# Is the nmos-cpp baseline an OPTIMIZED build?
#
#   ./bench_registry/check_baseline.sh [path-to-nmos-cpp-registry]
#
# A benchmark against an unoptimized C++ binary is worthless, and "I think it
# was a release build" is not evidence — the flags are not recorded anywhere
# obvious, because nmos-cpp's own translation units carry no DWARF producer
# string. So this infers the answer from the binary itself, and it is worth
# re-running whenever the baseline binary changes.
#
# Three independent signals, because no single one is conclusive:
#
#   1. NDEBUG — a build without it keeps glibc's assert(), which leaves
#      "__assert_fail" and "Assertion ... failed" strings behind.
#   2. Codegen — at -O0 GCC opens every function with a frame-pointer prologue
#      (`push %rbp` FOLLOWED BY `mov %rsp,%rbp`); at -O2 it almost never does.
#      Both thresholds are calibrated against known -O0 and -O2 builds.
#   3. Debug info — its presence alongside optimized codegen means
#      RelWithDebInfo rather than Debug, which is the normal shipping build.
#
# Exit 0 if the binary looks optimized, 1 if it does not.

set -Eeuo pipefail

BINARY="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/nmos-registry/nmos-cpp-registry}"

[ -f "$BINARY" ] || { echo "not found: $BINARY" >&2; exit 2; }
command -v objdump >/dev/null || { echo "objdump is required" >&2; exit 2; }

printf 'Baseline: %s\n' "$BINARY"
printf '  built:  %s\n\n' "$(stat -c '%y' "$BINARY" | cut -d' ' -f1)"

verdict=0

# --- 1. NDEBUG ------------------------------------------------------------
asserts="$(strings "$BINARY" | grep -cE '__assert_fail|Assertion .* failed' || true)"
if [ "$asserts" -eq 0 ]; then
    printf '  [ok]   NDEBUG defined (no glibc assertions compiled in)\n'
else
    printf '  [WARN] %s glibc assertion string(s) — NDEBUG may be undefined\n' "$asserts"
    verdict=1
fi

# --- 2. Codegen -----------------------------------------------------------
# The discriminator is the FRAME-POINTER PROLOGUE: the pair
#
#     push %rbp
#     mov  %rsp,%rbp
#
# At -O0 GCC emits it in essentially every function (measured ratio 1.00 on a
# calibration build); at -O2 it emits it almost never (0.00).
#
# It must be the PAIR. A bare `push %rbp` proves nothing -- optimized code uses
# %rbp as an ordinary callee-saved scratch register and pushes it constantly.
# Counting bare pushes gives ~0.82 on this very binary and reports an optimized
# build as unoptimized, which is exactly the wrong answer.
#
# Restricted to nmos:: functions on purpose: the binary statically links Boost,
# OpenSSL and cpprest, each built with its own flags, so a whole-binary ratio
# measures somebody else's build settings.
prologues="$(objdump -d --no-show-raw-insn "$BINARY" 2>/dev/null | awk '
    /^[0-9a-f]+ <_ZN4nmos/ { inside = 1; total++; prev = ""; next }
    /^[0-9a-f]+ </         { inside = 0; prev = ""; next }
    inside {
        if (prev ~ /push +%rbp/ && $0 ~ /mov +%rsp,%rbp/) frames++
        prev = $0
    }
    END { printf "%d %d", frames + 0, total + 0 }
')"
frames="${prologues% *}"
functions="${prologues#* }"

if [ "${functions:-0}" -lt 20 ]; then
    printf '  [WARN] only %s nmos:: functions found — cannot judge codegen\n' \
        "${functions:-0}"
    verdict=1
else
    ratio="$(awk -v f="$frames" -v n="$functions" 'BEGIN{printf "%.3f", f/n}')"
    # 0.35 sits well clear of both calibration points (-O0 ~1.00, -O2 ~0.00).
    if awk -v r="$ratio" 'BEGIN{exit !(r < 0.35)}'; then
        printf '  [ok]   optimized codegen (frame-pointer prologues in %s of %s nmos:: functions, ratio %s)\n' \
            "$frames" "$functions" "$ratio"
    else
        printf '  [WARN] looks UNOPTIMIZED (frame-pointer prologues in %s of %s nmos:: functions, ratio %s; -O0 is ~1.0)\n' \
            "$frames" "$functions" "$ratio"
        verdict=1
    fi
fi

# --- 3. Compiler identity -------------------------------------------------
compiler="$(readelf -p .comment "$BINARY" 2>/dev/null | grep -oE 'GCC: [^]]*' | head -1 || true)"
[ -n "$compiler" ] && printf '  [info] %s\n' "$compiler"

if readelf -S "$BINARY" 2>/dev/null | grep -q debug_info; then
    printf '  [info] debug info present — with the above, this is RelWithDebInfo\n'
else
    printf '  [info] no debug info — Release\n'
fi

echo
if [ "$verdict" -eq 0 ]; then
    echo "VERDICT: optimized. Fair to benchmark against."
    echo
    echo "  Still record the vintage: an older compiler and an older nmos-cpp"
    echo "  both cost it some performance. That shifts a result by percent, not"
    echo "  by a factor — and if nmos-cpp wins any phase outright, the build"
    echo "  cannot be badly unoptimized."
else
    echo "VERDICT: NOT clearly optimized. Do NOT publish a comparison against it."
    echo "  Rebuild with -DCMAKE_BUILD_TYPE=RelWithDebInfo, or use a release"
    echo "  container such as rhastie/nmos-cpp."
fi
exit "$verdict"
