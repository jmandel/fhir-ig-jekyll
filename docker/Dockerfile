# Reference build/test image: the FHIR autobuilder's localdev image (Jekyll 4.4.1 / Liquid 4.0.4 /
# kramdown 2.5.2 / Ruby 3.2.3, JDK 21), pinned by digest (docs/TRACKER.md D9), plus python3.
# Tag it `ig-publisher-localdev-ca` (the scripts use that name).
#
# Behind a TLS-inspecting proxy? Put the proxy's root CA certificate(s) as *.crt or *.pem files in
# docker/extra-ca/ before building; they are trusted by the OS, Java and Node. The directory may be empty.
#
#   docker build -t ig-publisher-localdev-ca docker/
#
# Drift checks only: --build-arg BASE=ghcr.io/fhir/ig-publisher-localdev:latest
ARG BASE=ghcr.io/fhir/ig-publisher-localdev@sha256:39509f4138197f0b93a99ed3ae00e6dce2bbd76df1a2e4e2bd6dec4180dc34b5
FROM ${BASE}
USER root
# python3: some templates' ant scripts shell out to it (WHO smart-ig-template); the CI image has it too.
RUN apt-get update && apt-get -y --no-install-recommends install python3 && rm -rf /var/lib/apt/lists/*
COPY extra-ca/ /tmp/extra-ca/
RUN set -e; n=0; for f in /tmp/extra-ca/*.crt /tmp/extra-ca/*.pem; do \
      [ -f "$f" ] || continue; n=$((n+1)); \
      cp "$f" "/usr/local/share/ca-certificates/extra-ca-$n.crt"; \
      keytool -importcert -noprompt -trustcacerts -alias "extra-ca-$n" -file "$f" -cacerts -storepass changeit; \
      cat "$f" >> /usr/local/share/ca-certificates/extra-ca-bundle.pem; \
    done; update-ca-certificates; touch /usr/local/share/ca-certificates/extra-ca-bundle.pem; rm -rf /tmp/extra-ca
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/extra-ca-bundle.pem \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
