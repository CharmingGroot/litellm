#!/bin/sh
# Build a perkeep client config that signs with the *server's* identity and keyring
# (mounted read-only at /perkeep-config), so the permanodes pk-put creates are owned
# by the server and show up in its index and UI. Wait for perkeep's first-boot to
# generate the identity before reading it.
set -e

SRV_CFG=/perkeep-config/server-config.json
SECRING=/perkeep-config/identity-secring.gpg
while [ ! -f "$SRV_CFG" ] || [ ! -f "$SECRING" ]; do sleep 1; done

IDENTITY=$(sed -n 's/.*"identity": "\([^"]*\)".*/\1/p' "$SRV_CFG" | head -1)
mkdir -p "$HOME/.config/perkeep"
cat > "$HOME/.config/perkeep/client-config.json" <<EOF
{
  "servers": {
    "default": {
      "server": "${PERKEEP_SERVER}",
      "auth": "${CAMLI_AUTH}",
      "default": true
    }
  },
  "identity": "${IDENTITY}",
  "identitySecretRing": "${SECRING}"
}
EOF

exec uvicorn app:app --host 0.0.0.0 --port 8080 --no-access-log
