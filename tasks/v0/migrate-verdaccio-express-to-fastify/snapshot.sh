#!/usr/bin/env bash
# Generate the Verdaccio pre-Fastify snapshot tarball.
# Run from the task directory: ./snapshot.sh
#
# The tarball is gitignored — run this script to regenerate it
# before running the benchmark.
set -euo pipefail

COMMIT="8db9cf93cef495dc75db68b5a9044c7ed3313f01"
OUTPUT="verdaccio-pre-fastify.tar.gz"
TMPDIR=$(mktemp -d)
TASK_DIR=$(pwd)

echo "Cloning verdaccio/verdaccio..."
git clone https://github.com/verdaccio/verdaccio.git "${TMPDIR}/verdaccio" 2>&1

echo "Checking out pre-Fastify commit ${COMMIT}..."
cd "${TMPDIR}/verdaccio"
git checkout "${COMMIT}" 2>&1

echo "Removing symlink fixtures from snapshot..."
find . -type l -print -delete >/dev/null 2>&1 || true

echo "Creating ${OUTPUT}..."
cd "${TMPDIR}"
tar czf "${OUTPUT}" --exclude='.git' -C "${TMPDIR}/verdaccio" .

mv "${OUTPUT}" "${TASK_DIR}/"
rm -rf "${TMPDIR}"
cd "${TASK_DIR}"
echo "Done: $(ls -lh "${OUTPUT}" | awk '{print $5}') ${OUTPUT}"
