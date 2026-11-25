# Generate a private CA and an MQTT/TLS server certificate for the golden ThingsBoard stack.
#
# Usage:
#   bash certs/gen_certs.sh [HOST] [IP]
#
# Defaults: HOST=localhost, IP=127.0.0.1
# OUT_DIR may be overridden (used by the cert-init service in compose.yaml).
#
# Produces: ca.pem, ca_key.pem, server.pem, server_key.pem
# The SAN intentionally contains BOTH localhost and thingsboard-ce so that:
#   - host-side tests using --host localhost verify correctly
#   - edge containers using MQTT_HOST=thingsboard-ce verify correctly

set -euo pipefail

OUT_DIR="${OUT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

HOST="${1:-localhost}"
IP="${2:-127.0.0.1}"

# Silence noisy key-generation progress; errors still stop the script.
exec 3>&2 2>/dev/null

# CA certificate. keyCertSign is required by strict Python 3.13 and modern mbedTLS.
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -subj "/CN=JalDrishti CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" \
  -keyout ca_key.pem -out ca.pem

# Server key and CSR.
openssl req -newkey rsa:2048 -nodes \
  -subj "/CN=${HOST}" \
  -keyout server_key.pem -out server.csr

# SAN is what clients actually check. Keep both names in every generated cert.
SAN="DNS:localhost,DNS:thingsboard-ce,IP:${IP}"
case "$HOST" in
  localhost|thingsboard-ce) ;;
  *) SAN="${SAN},DNS:${HOST}" ;;
esac

cat > san.ext <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
authorityKeyIdentifier=keyid,issuer
subjectAltName=${SAN}
EOF

openssl x509 -req -in server.csr \
  -CA ca.pem -CAkey ca_key.pem -CAcreateserial \
  -days 825 -sha256 -extfile san.ext -out server.pem

# CA key stays private. Server key must be readable by the ThingsBoard user in the container.
chmod 600 ca_key.pem
chmod 644 ca.pem server.pem server_key.pem

rm -f server.csr san.ext ca.srl

echo "Generated CA and server certificate in: $OUT_DIR" >&3
echo "Certificate SAN: $SAN" >&3