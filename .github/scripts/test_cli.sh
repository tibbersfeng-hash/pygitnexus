#!/usr/bin/env bash
# Cross-platform CLI smoke test for PyGitNexus CI
# Called from: .github/workflows/build.yml
# Usage: test_cli.sh [binary_path] [tag_version]

set +e

# Failure tracking
FAILURES=0
TOTAL=0

# Accept binary path as first argument (defaults to dist/pygitnexus)
BINARY="${1:-dist/pygitnexus}"

# Detect platform and set binary extension
OS_NAME="$(uname -s)"
if [[ "$OS_NAME" == MINGW* ]] || [[ "$OS_NAME" == MSYS* ]]; then
    # If binary doesn't already have .exe, add it
    case "$BINARY" in
        *.exe) ;;
        *) BINARY="${BINARY}.exe" ;;
    esac
fi

TEST_DIR="tests/fixtures/simple"
TAG_MAJOR="${2:-${GITHUB_REF_NAME#v}}"

# Extension for temp binaries created during install tests
if [[ "$OS_NAME" == MINGW* ]] || [[ "$OS_NAME" == MSYS* ]]; then
    EXT=".exe"
else
    EXT=""
fi

echo "=== pygitnexus CLI Smoke Test ==="
echo "Platform: $OS_NAME"
echo "PWD: $(pwd)"
echo "Binary: $BINARY"
echo "Binary exists: $(test -f "$BINARY" && echo yes || echo no)"
echo "Test fixtures: $(test -d "$TEST_DIR" && echo yes || echo no)"
echo ""

# Helper: run command, capture exit code, print output, track failures
run_cmd() {
    local label="$1"
    shift
    TOTAL=$((TOTAL + 1))
    echo "----------------------------------------"
    echo "$label"
    echo "----------------------------------------"
    "$@" > /tmp/_pgn_out.txt 2>&1
    rc=$?
    cat /tmp/_pgn_out.txt
    echo ""
    if [ "$rc" -eq 0 ]; then
        echo "  [PASS] $label (exit code: $rc)"
    else
        echo "  [FAIL] $label (exit code: $rc)"
        FAILURES=$((FAILURES + 1))
    fi
    echo ""
}

# Helper: run command, capture both output and exit code into globals
run_capture() {
    local label="$1"
    shift
    TOTAL=$((TOTAL + 1))
    echo "----------------------------------------"
    echo "$label"
    echo "----------------------------------------"
    "$@" > /tmp/_pgn_out.txt 2>&1
    CAPTURE_RC=$?
    CAPTURE_OUT=$(cat /tmp/_pgn_out.txt)
    echo "$CAPTURE_OUT"
    echo ""
    if [ "$CAPTURE_RC" -eq 0 ]; then
        echo "  [PASS] $label (exit code: $CAPTURE_RC)"
    else
        echo "  [FAIL] $label (exit code: $CAPTURE_RC)"
        FAILURES=$((FAILURES + 1))
    fi
    echo ""
}

# Save absolute path to project root and binary
PROJECT_ROOT="$(pwd)"
BINARY="$PROJECT_ROOT/$BINARY"

# 0. Sanity: --version
run_cmd "[0/12] Sanity: binary --version" "$BINARY" --version

# 1. analyze
run_cmd "[1/12] Testing: analyze" "$BINARY" analyze "$TEST_DIR"

# 2. list
run_cmd "[2/12] Testing: list" "$BINARY" list

# 3-5. query, cypher, context — must run in test dir to find the DB
cd "$PROJECT_ROOT/$TEST_DIR" 2>/dev/null
run_cmd "[3/12] Testing: query" "$BINARY" query "User"
run_cmd "[4/12] Testing: cypher" "$BINARY" cypher "MATCH (n) RETURN count(n)"
run_cmd "[5/12] Testing: context" "$BINARY" context "main"
cd "$PROJECT_ROOT" 2>/dev/null

# 6. status
cd "$PROJECT_ROOT/$TEST_DIR" 2>/dev/null
run_cmd "[6/12] Testing: status" "$BINARY" status
cd "$PROJECT_ROOT" 2>/dev/null

# 7. clean
run_cmd "[7/12] Testing: clean" "$BINARY" clean --force

# 8. install --file
INST_DIR="$PWD/_test_install"
mkdir -p "$INST_DIR"
run_capture "[8/12] Testing: install --file" "$BINARY" install --file "$BINARY" --path "$INST_DIR" --force
if [ -f "$INST_DIR/pygitnexus${EXT}" ]; then
    VER_OUT=$("$INST_DIR/pygitnexus${EXT}" --version 2>/dev/null)
    echo "  Checkpoint: installed --version = $VER_OUT"
    if [ -n "$VER_OUT" ]; then
        echo "  PASS CHECKPOINT: version non-empty"
    fi
else
    echo "  WARN: binary not copied"
fi
rm -rf "$INST_DIR"
echo ""

# 9. install self (copy binary first, then self-install)
INST_DIR2="$PWD/_test_self_src"
mkdir -p "$INST_DIR2"
cp "$BINARY" "$INST_DIR2/pygitnexus${EXT}"
SELF_DIR="$PWD/_test_self"
mkdir -p "$SELF_DIR"
run_capture "[9/12] Testing: install self" "$INST_DIR2/pygitnexus${EXT}" install --path "$SELF_DIR" --force
if [ -f "$SELF_DIR/pygitnexus${EXT}" ]; then
    VER_OUT=$("$SELF_DIR/pygitnexus${EXT}" --version 2>/dev/null)
    VM="${VER_OUT%%.*}"
    echo "  Checkpoint: self-install --version = $VER_OUT (major=$VM), tag major=$TAG_MAJOR"
    if [ "$VM" = "$TAG_MAJOR" ]; then
        echo "  PASS CHECKPOINT: version matches tag major"
    elif [ -n "$VER_OUT" ]; then
        echo "  WARN: version mismatch (expected for dev builds)"
    fi
else
    echo "  WARN: self-installed binary not found"
fi
rm -rf "$INST_DIR2" "$SELF_DIR"
echo ""

# 10. install --version latest
VER_DIR="$PWD/_test_ver"
mkdir -p "$VER_DIR"
run_capture "[10/12] Testing: install --version latest" "$BINARY" install --version latest --path "$VER_DIR" --force
if [ -f "$VER_DIR/pygitnexus${EXT}" ]; then
    VER_OUT=$("$VER_DIR/pygitnexus${EXT}" --version 2>/dev/null)
    VM="${VER_OUT%%.*}"
    echo "  Checkpoint: download --version = $VER_OUT (major=$VM), tag major=$TAG_MAJOR"
    if [ "$VM" = "$TAG_MAJOR" ]; then
        echo "  PASS CHECKPOINT: download version matches tag major"
    elif [ -n "$VER_OUT" ]; then
        echo "  WARN: download version mismatch"
    fi
else
    echo "  WARN: downloaded binary not found"
fi
rm -rf "$VER_DIR"
echo ""

# 11. web (starts HTTP server, runs briefly then killed)
TOTAL=$((TOTAL + 1))
echo "----------------------------------------"
echo "[11/12] Testing: web"
echo "----------------------------------------"
"$BINARY" web --port 18765 --no-open > /tmp/_pgn_web.txt 2>&1 &
WEB_PID=$!
sleep 3
kill $WEB_PID 2>/dev/null
WEB_OUT=$(cat /tmp/_pgn_web.txt)
echo "$WEB_OUT"
if echo "$WEB_OUT" | grep -qi "dashboard\|uvicorn\|18765\|starting"; then
    echo "  [PASS] web command started"
else
    echo "  [FAIL] web command output unexpected"
    FAILURES=$((FAILURES + 1))
fi
echo ""

echo "========================================"
if [ "$FAILURES" -eq 0 ]; then
    echo " All $TOTAL CLI commands passed"
    echo "========================================"
    exit 0
else
    echo " $FAILURES/$TOTAL commands FAILED"
    echo "========================================"
    exit 1
fi
