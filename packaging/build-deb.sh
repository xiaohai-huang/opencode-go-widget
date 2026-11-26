#!/bin/sh
# Build a binary .deb into dist/.
#
#   ./packaging/build-deb.sh
#
# Build dependencies (Debian/Ubuntu):
#   sudo apt install build-essential debhelper dpkg-dev fakeroot
set -eu

cd "$(dirname "$0")/.."

for tool in dpkg-buildpackage dh fakeroot; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: $tool is missing." >&2
        echo "On Debian/Ubuntu: sudo apt install build-essential debhelper dpkg-dev fakeroot" >&2
        exit 1
    fi
done

APP_VERSION=$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' app/ocg/__init__.py)
DEB_VERSION=$(dpkg-parsechangelog -SVersion)
if [ "$APP_VERSION" != "$DEB_VERSION" ]; then
    echo "error: app version '$APP_VERSION' != debian/changelog version '$DEB_VERSION'" >&2
    exit 1
fi

rm -rf dist
dpkg-buildpackage -b -us -uc

mkdir -p dist
mv ../opencode-go-widget_*.deb dist/
mv ../opencode-go-widget_*.changes dist/ 2>/dev/null || true
mv ../opencode-go-widget_*.buildinfo dist/ 2>/dev/null || true

echo
echo "Built:"
ls -l dist/*.deb
