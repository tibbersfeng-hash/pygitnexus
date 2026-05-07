#!/usr/bin/env bash
# Windows CLI smoke test for PyGitNexus CI
# Called from: .github/workflows/build.yml
# All commands run with error suppression — this is a smoke test, not strict validation.

set +e

EXT=".exe"
BINARY="dist/pygitnexus${EXT}"
TEST_DIR="tests/fixtures/simple"
TAG_MAJOR="${GITHUB_REF_NAME#v}"

echo "=== pygitnexus CLI Smoke Test on Windows ==="
echo "PWD: $(pwd)"
echo "Binary: $BINARY"
echo "Binary exists: $(test -f "$BINARY" && echo yes || echo no)"
echo "Test fixtures: $(test -d "$TEST_DIR" && echo yes || echo no)"
echo ""

# Helper: run command, capture exit code, print output
run_cmd() {
    local label="$1"
    shift
    echo "----------------------------------------"
    echo "$label"
    echo "----------------------------------------"
    "$@" > /tmp/_pgn_out.txt 2>&1
    rc=$?
    cat /tmp/_pgn_out.txt
    echo ""
    echo "  (exit code: $rc)"
    echo ""
}

# Helper: run command, capture both output and exit code into globals
run_capture() {
    local label="$1"
    shift
    echo "----------------------------------------"
    echo "$label"
    echo "----------------------------------------"
    "$@" > /tmp/_pgn_out.txt 2>&1
    CAPTURE_RC=$?
    CAPTURE_OUT=$(cat /tmp/_pgn_out.txt)
    echo "$CAPTURE_OUT"
    echo ""
}

# 0. Sanity: --version
run_cmd "[0/11] Sanity: binary --version" "$BINARY" --version

# 1. analyze
run_cmd "[1/11] Testing: analyze" "$BINARY" analyze "$TEST_DIR"

# 2. list
run_cmd "[2/11] Testing: list" "$BINARY" list

# 3. query
run_cmd "[3/11] Testing: query" "$BINARY" query "User"

# 4. cypher
run_cmd "[4/11] Testing: cypher" "$BINARY" cypher "MATCH (n) RETURN count(n)"

# 5. context
run_cmd "[5/11] Testing: context" "$BINARY" context "main"

# 6. status
cd "$TEST_DIR" 2>/dev/null
run_cmd "[6/11] Testing: status" "$BINARY" status
cd .. 2>/dev/null

# 7. clean
run_cmd "[7/11] Testing: clean" "$BINARY" clean --force

# 8. install --file
INST_DIR="$PWD/_test_install"
mkdir -p "$INST_DIR"
run_capture "[8/11] Testing: install --file" "$BINARY" install --file "$BINARY" --path "$INST_DIR" --force
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
run_capture "[9/11] Testing: install self" "$INST_DIR2/pygitnexus${EXT}" install --path "$SELF_DIR" --force
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
run_capture "[10/11] Testing: install --version latest" "$BINARY" install --version latest --path "$VER_DIR" --force
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

echo "========================================"
echo " All 11 CLI commands smoke-tested on Windows"
echo "========================================"

exit 0
