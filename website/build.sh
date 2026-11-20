#!/usr/bin/env bash
# Build the website into _site/: the landing page at the top, the documentation under docs/.
#   website/build.sh            build it
#   website/build.sh --serve    build it, then serve it at http://localhost:8790
set -euo pipefail
cd "$(dirname "$0")/.."
export NO_MKDOCS_2_WARNING=1

rm -rf _site
mkdir -p _site
cp -R website/home/. _site/
uv run --quiet --no-project --with-requirements website/requirements.txt \
  mkdocs build --strict --quiet -f website/mkdocs.yml

echo "Built _site/ ($(find _site -name '*.html' | wc -l | tr -d ' ') pages)"
if [[ "${1:-}" == "--serve" ]]; then
  python3 -m http.server 8790 --directory _site
fi
