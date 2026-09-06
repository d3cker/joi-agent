#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="${VOICE_AGENT_DIST_DIR:-$PROJECT_DIR/dist}"
REPOSITORY_DIR="$(cd "$PROJECT_DIR/../.." && pwd)"
RELEASE_VERSION="$(tr -d '\n' < "$REPOSITORY_DIR/VERSION")"
APP_DIR="$DIST_DIR/VoiceAgent.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
BUILD_ROOT="$PROJECT_DIR/.build"
MODULE_CACHE="$BUILD_ROOT/module-cache"
SWIFTC="$(xcrun --find swiftc)"
HOST_ARCH="$(uname -m)"
TARGET="$HOST_ARCH-apple-macosx13.0"

mkdir -p "$MODULE_CACHE"
export CLANG_MODULE_CACHE_PATH="$MODULE_CACHE"
export SWIFTPM_MODULECACHE_OVERRIDE="$MODULE_CACHE"

build_direct() {
    local sdk="$1"
    local sdk_name
    local direct_dir
    local log_file
    local core_sources
    local app_sources

    sdk_name="$(basename "$sdk" .sdk)"
    direct_dir="$BUILD_ROOT/direct-$sdk_name-$HOST_ARCH"
    log_file="$direct_dir/build.log"
    core_sources=("$PROJECT_DIR"/Sources/VoiceAgentCore/*.swift)
    app_sources=("$PROJECT_DIR"/Sources/VoiceAgent/*.swift)
    mkdir -p "$direct_dir"

    if "$SWIFTC" \
        -swift-version 5 \
        -sdk "$sdk" \
        -target "$TARGET" \
        -module-cache-path "$MODULE_CACHE" \
        -emit-module \
        -emit-library \
        -static \
        -module-name VoiceAgentCore \
        "${core_sources[@]}" \
        -o "$direct_dir/libVoiceAgentCore.a" \
        >"$log_file" 2>&1 \
        && "$SWIFTC" \
        -swift-version 5 \
        -sdk "$sdk" \
        -target "$TARGET" \
        -module-cache-path "$MODULE_CACHE" \
        -parse-as-library \
        -I "$direct_dir" \
        "${app_sources[@]}" \
        "$direct_dir/libVoiceAgentCore.a" \
        -o "$direct_dir/VoiceAgent" \
        >>"$log_file" 2>&1; then
        BUILT_BINARY="$direct_dir/VoiceAgent"
        echo "Fallback swiftc: użyto $sdk_name ($HOST_ARCH)."
        return 0
    fi

    echo "Fallback swiftc: $sdk_name nie jest zgodny z tym toolchainem."
    return 1
}

cd "$PROJECT_DIR"
SWIFTPM_LOG="$BUILD_ROOT/swiftpm-release.log"
if swift build -c release >"$SWIFTPM_LOG" 2>&1; then
    BIN_DIR="$(swift build -c release --show-bin-path)"
    BUILT_BINARY="$BIN_DIR/VoiceAgent"
else
    echo "SwiftPM nie zbudował aplikacji; próbuję zgodnych lokalnych SDK."
    BUILT_BINARY=""
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
        if build_direct "$sdk"; then
            break
        fi
    done

    if [[ -z "$BUILT_BINARY" ]]; then
        echo "Nie znaleziono SDK zgodnego z lokalnym kompilatorem Swift." >&2
        echo "Szczegóły SwiftPM: $SWIFTPM_LOG" >&2
        exit 1
    fi
fi

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"
cp "$PROJECT_DIR/AppBundle/Info.plist" "$CONTENTS_DIR/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $RELEASE_VERSION" "$CONTENTS_DIR/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${JOI_BUILD_NUMBER:-$RELEASE_VERSION}" "$CONTENTS_DIR/Info.plist"
cp "$BUILT_BINARY" "$MACOS_DIR/VoiceAgent"
rsync -a --delete "$PROJECT_DIR/Resources/" "$RESOURCES_DIR/"
chmod 755 "$MACOS_DIR/VoiceAgent"
if [[ -n "${JOI_SIGN_IDENTITY:-}" ]]; then
    codesign --force --options runtime --timestamp --entitlements "$PROJECT_DIR/AppBundle/VoiceAgent.entitlements" --sign "$JOI_SIGN_IDENTITY" "$APP_DIR"
else
    codesign --force --sign - "$APP_DIR"
fi
codesign --verify --strict "$APP_DIR"

echo "Gotowe: $APP_DIR"
