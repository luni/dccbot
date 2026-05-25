#!/bin/sh
set -e

mkdir -p /inspircd/certs

if [ ! -f /inspircd/certs/cert.pem ] || [ ! -f /inspircd/certs/key.pem ]; then
    echo 'Generating self-signed SSL certificate...'
    openssl req -x509 -newkey rsa:4096 \
        -keyout /inspircd/certs/key.pem \
        -out /inspircd/certs/cert.pem \
        -days 1 -nodes \
        -subj '/CN=test.local' 2>/dev/null || \
        echo 'SSL cert generation may have failed, continuing...'
fi

# Disable modules that Z-line connections during integration tests
sed -i 's|^<module name="connflood">|#<module name="connflood">|g' /conf/modules.conf
sed -i 's|^<module name="connectban">|#<module name="connectban">|g' /conf/modules.conf

exec /entrypoint.sh
