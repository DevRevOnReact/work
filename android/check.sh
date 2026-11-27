#!/usr/bin/env bash
set -euo pipefail
sdk="${ANDROID_HOME:?Android SDK required}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
tools="$sdk/build-tools/35.0.0"
platform="$sdk/platforms/android-35/android.jar"
mkdir -p "$work/gen" "$work/classes" "$work/tests"
"$tools/aapt2" compile --dir android/res -o "$work/resources.zip"
"$tools/aapt2" link -o "$work/resources.apk" -I "$platform" --manifest android/AndroidManifest.xml --java "$work/gen" "$work/resources.zip"
find android/src "$work/gen" -name '*.java' > "$work/sources"
javac --release 8 -encoding UTF-8 -classpath "$platform" -d "$work/classes" @"$work/sources"
javac -d "$work/tests" android/src/dev/blyadota/jobpilot/assistant/CapturePixels.java android/tests/CapturePixelsTest.java
java -ea -cp "$work/tests" dev.blyadota.jobpilot.assistant.CapturePixelsTest
