#!/usr/bin/env bash
set -euo pipefail

: "${ANDROID_BUILD_TOP:=/work/aosp}"
: "${AOSP_BRANCH:=android-13.0.0_r1}"
export ANDROID_BUILD_TOP AOSP_BRANCH

mkdir -p "$(dirname "$ANDROID_BUILD_TOP")"
if [[ ! -d "$ANDROID_BUILD_TOP/.repo" ]]; then
  mkdir -p "$ANDROID_BUILD_TOP"
  repo init -u https://android.googlesource.com/platform/manifest -b "$AOSP_BRANCH"
fi

cd "$ANDROID_BUILD_TOP"
repo sync -c --no-clone-bundle --no-tags -j2

source build/envsetup.sh
lunch aosp_arm64-userdebug
m systemimage -j2

OUT_DIR="out/target/product/generic_arm64"
if [[ ! -f "$OUT_DIR/system.img" ]]; then
  echo "ERROR: Expected system.img was not produced at $OUT_DIR/system.img" >&2
  find out/target/product -maxdepth 3 -name 'system*.img' -print || true
  exit 1
fi

mkdir -p "$GITHUB_WORKSPACE/dist"
cp "$OUT_DIR/system.img" "$GITHUB_WORKSPACE/dist/android13-aosp-arm64-system.img"
sha256sum "$GITHUB_WORKSPACE/dist/android13-aosp-arm64-system.img" > "$GITHUB_WORKSPACE/dist/SHA256SUMS.txt"
