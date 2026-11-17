#!/usr/bin/env bash
# Builds the Android app for release and signs it with your own key.
#
#   TUCKVAULT_KEYSTORE=/path/to/tuckvault-release.jks \
#   TUCKVAULT_KEYSTORE_PASSWORD_FILE=/path/to/keystore-password.txt \
#   bash mobile/tools/release-android.sh
#
# Needs JAVA_HOME (JDK 17) and ANDROID_HOME. The result is
# mobile/dist/Tuckvault-<version>-android.apk with its SHA-256 beside it.
#
# The key never enters the repository or the build folder: the app is built
# with the throwaway debug key and then signed again with yours. Android only
# installs an update when it is signed with the same key as the installed app,
# so keep the key and its password safe.
set -euo pipefail

cd "$(dirname "$0")/.."
: "${TUCKVAULT_KEYSTORE:?set TUCKVAULT_KEYSTORE to the keystore file}"
: "${TUCKVAULT_KEYSTORE_PASSWORD_FILE:?set TUCKVAULT_KEYSTORE_PASSWORD_FILE to the file holding its password}"
: "${ANDROID_HOME:?set ANDROID_HOME to the Android SDK}"
ALIAS="${TUCKVAULT_KEY_ALIAS:-tuckvault}"
VERSION=$(node -p "require('./app.json').expo.version")

npx expo prebuild --platform android --no-install
(cd android && ./gradlew :tuckvault-core:testDebugUnitTest :app:assembleRelease \
  -PreactNativeArchitectures=arm64-v8a,armeabi-v7a,x86_64 --console=plain)

TOOLS=$(ls -d "$ANDROID_HOME"/build-tools/* | sort -V | tail -1)
SIGNER="$TOOLS/apksigner"
[ -f "$SIGNER.bat" ] && SIGNER="$SIGNER.bat"
mkdir -p dist
OUT="dist/Tuckvault-$VERSION-android.apk"
"$SIGNER" sign --ks "$TUCKVAULT_KEYSTORE" --ks-key-alias "$ALIAS" \
  --ks-pass "file:$TUCKVAULT_KEYSTORE_PASSWORD_FILE" --key-pass "file:$TUCKVAULT_KEYSTORE_PASSWORD_FILE" \
  --out "$OUT" android/app/build/outputs/apk/release/app-release.apk
"$SIGNER" verify --print-certs "$OUT" | grep -E "SHA-256|Signer #1 certificate DN"
rm -f "$OUT.idsig"
(cd dist && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" && cat "$(basename "$OUT").sha256")
echo "Built $OUT"
