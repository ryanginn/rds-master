#!/bin/bash
# Wrap the frozen build into a .pkg.
#
#     bash build_pkg.sh <version> <frozen-dir> <output-dir>
#
# NOTE: this has never been run. It is written from Apple's documentation and
# needs trying on a real Mac before anyone relies on it.
#
# Unsigned, so Gatekeeper will refuse it on a current macOS until the user
# right-clicks and chooses Open, or you sign and notarise:
#     productsign --sign "Developer ID Installer: ..." in.pkg out.pkg
#     xcrun notarytool submit out.pkg --apple-id ... --wait
set -euo pipefail

VERSION="${1:?version}"
FROZEN="${2:?frozen directory}"
OUTPUT="${3:?output directory}"
HERE="$(cd "$(dirname "$0")" && pwd)"

ROOT="$(mktemp -d)"
APP="$ROOT/Applications/RDS Master.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp -R "$FROZEN"/* "$APP/Contents/MacOS/"
cp "$HERE/../icons/rdsm.icns" "$APP/Contents/Resources/rdsm.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>RDS Master</string>
    <key>CFBundleIdentifier</key>      <string>ie.fmdx.rdsmaster</string>
    <key>CFBundleVersion</key>         <string>$VERSION</string>
    <key>CFBundleShortVersionString</key> <string>$VERSION</string>
    <key>CFBundleExecutable</key>      <string>rds-master-tray</string>
    <key>CFBundleIconFile</key>        <string>rdsm.icns</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <!-- The tray icon lives in the menu bar and has no dock tile or window. -->
    <key>LSUIElement</key>             <true/>
    <!-- macOS 14 and later ask before letting anything record audio. -->
    <key>NSMicrophoneUsageDescription</key>
    <string>RDS Master reads an audio input to generate the RDS subcarrier.</string>
</dict>
</plist>
PLIST

mkdir -p "$OUTPUT"
pkgbuild --root "$ROOT" \
         --identifier ie.fmdx.rdsmaster \
         --version "$VERSION" \
         --install-location / \
         "$OUTPUT/rds-master-$VERSION.pkg"

echo "built $OUTPUT/rds-master-$VERSION.pkg (unsigned)"
