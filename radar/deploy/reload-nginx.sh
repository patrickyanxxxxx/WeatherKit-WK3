#!/bin/sh
set -eu
# This VPS's shared public nginx is the existing host-network Docker container.
docker exec nginx nginx -t
docker exec nginx nginx -s reload
