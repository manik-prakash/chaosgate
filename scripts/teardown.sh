#!/usr/bin/env bash
# Delete the demo cluster and everything in it.
set -euo pipefail
kind delete cluster --name "${CLUSTER:-chaosgate}"
