#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
release="$(git rev-parse --short=12 HEAD)"
for service in api web backup; do
    docker image tag "koravto-crm-$service:current" "koravto-crm-$service:$release"
done
printf 'Release images preserved: %s\n' "$release"
