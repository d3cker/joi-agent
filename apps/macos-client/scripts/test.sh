#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_ROOT="$PROJECT_DIR/.build"
MODULE_CACHE="$BUILD_ROOT/module-cache"
SWIFT_TEST_LOG="$BUILD_ROOT/swift-test.log"
SWIFTC="$(xcrun --find swiftc)"
HOST_ARCH="$(uname -m)"
TARGET="$HOST_ARCH-apple-macosx13.0"

mkdir -p "$MODULE_CACHE"
export CLANG_MODULE_CACHE_PATH="$MODULE_CACHE"
export SWIFTPM_MODULECACHE_OVERRIDE="$MODULE_CACHE"

cd "$PROJECT_DIR"
if swift test >"$SWIFT_TEST_LOG" 2>&1; then
    cat "$SWIFT_TEST_LOG"
    echo "macOS XCTest: PASS"
    exit 0
fi

if [[ "${JOI_STRICT_TESTS:-0}" == "1" ]]; then
    cat "$SWIFT_TEST_LOG" >&2
    exit 1
fi

KNOWN_CLT_ERROR='SDK is not supported by the compiler|Invalid manifest|PackageDescription|no such module .XCTest.|unable to open output file.*ModuleCache|failed loading cached manifest'
if ! grep -Eq "$KNOWN_CLT_ERROR" "$SWIFT_TEST_LOG"; then
    cat "$SWIFT_TEST_LOG" >&2
    echo "swift test zakończył się nieznanym błędem; fallback nie został uruchomiony." >&2
    exit 1
fi

echo "macOS XCTest: SKIPPED (rozpoznana niezgodność lokalnych Command Line Tools)."
echo "Uruchamiam ograniczony fallback VoiceAgentCore. Szczegóły: $SWIFT_TEST_LOG"

SDK_CANDIDATES=()
if [[ -n "${VOICE_AGENT_SDK_PATH:-}" ]]; then
    SDK_CANDIDATES+=("$VOICE_AGENT_SDK_PATH")
else
    DEFAULT_SDK="$(xcrun --sdk macosx --show-sdk-path)"
    SDK_CANDIDATES+=("$DEFAULT_SDK")
    SDK_ROOT="$(dirname "$DEFAULT_SDK")"
    while IFS= read -r sdk; do
        SDK_CANDIDATES+=("$sdk")
    done < <(find "$SDK_ROOT" -maxdepth 1 -type d -name 'MacOSX*.sdk' -print | sort -Vr)
fi

for sdk in "${SDK_CANDIDATES[@]}"; do
    [[ -d "$sdk" ]] || continue
    SDK_NAME="$(basename "$sdk" .sdk)"
    DIRECT_DIR="$BUILD_ROOT/core-smoke-$SDK_NAME-$HOST_ARCH"
    DIRECT_LOG="$DIRECT_DIR/build.log"
    mkdir -p "$DIRECT_DIR"

    if "$SWIFTC" \
        -swift-version 5 \
        -sdk "$sdk" \
        -target "$TARGET" \
        -module-cache-path "$MODULE_CACHE" \
        -emit-module \
        -emit-library \
        -static \
        -module-name VoiceAgentCore \
        "$PROJECT_DIR"/Sources/VoiceAgentCore/*.swift \
        -o "$DIRECT_DIR/libVoiceAgentCore.a" \
        >"$DIRECT_LOG" 2>&1 \
        && "$SWIFTC" \
        -swift-version 5 \
        -sdk "$sdk" \
        -target "$TARGET" \
        -module-cache-path "$MODULE_CACHE" \
        -I "$DIRECT_DIR" \
        "$PROJECT_DIR/Tests/VoiceAgentCoreSmoke/main.swift" \
        "$DIRECT_DIR/libVoiceAgentCore.a" \
        -o "$DIRECT_DIR/VoiceAgentCoreSmoke" \
        >>"$DIRECT_LOG" 2>&1; then
        "$DIRECT_DIR/VoiceAgentCoreSmoke"
        echo "macOS test fallback: PASS ($SDK_NAME, $HOST_ARCH)"
        exit 0
    fi
done

echo "Nie znaleziono SDK pozwalającego uruchomić fallback VoiceAgentCore." >&2
exit 1
