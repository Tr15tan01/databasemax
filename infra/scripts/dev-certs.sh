#!/usr/bin/env bash
# Generates a local development CA and a gateway certificate for localhost and *.db.localhost.
# Production uses a publicly trusted wildcard certificate (see docs/deployment/oci.md).
set -euo pipefail
OUT="${1:-$(dirname "$0")/../../.dev/certs}"
mkdir -p "$OUT"
cd "$OUT"
if [ ! -f ca.key ]; then
  openssl genrsa -out ca.key 3072 2>/dev/null
  openssl req -x509 -new -key ca.key -sha256 -days 825 -subj "/CN=DatabaseMax Dev CA" -out ca.crt
fi
openssl genrsa -out gateway.key 2048 2>/dev/null
cat > gateway.ext <<EXT
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,DNS:*.db.localhost,DNS:db.localhost,IP:127.0.0.1
EXT
openssl req -new -key gateway.key -subj "/CN=db.localhost" -out gateway.csr
openssl x509 -req -in gateway.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 397 -sha256 \
  -extfile gateway.ext -out gateway.crt 2>/dev/null
chmod 600 ca.key
# Development only: the gateway container runs as an unprivileged user and must read this key.
# In production the key is root:<gateway gid> 0640 (see docs/deployment/oci.md).
chmod 644 gateway.key
rm -f gateway.csr gateway.ext
echo "certificates written to $OUT"
