#!/bin/zsh
set -euo pipefail

IP=$(/sbin/ifconfig | awk '/inet /{print $2}' | grep -v '^127\.' | head -n1)
if [[ -z "${IP}" ]]; then
  echo "No non-loopback IP found"
  exit 1
fi

exec /usr/bin/dns-sd -P "openclaw-usage" "_http._tcp" "local." 8090 "openclaw-usage.local." "${IP}" "path=/"
