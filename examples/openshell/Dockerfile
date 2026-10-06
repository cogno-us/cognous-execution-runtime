# SPDX-License-Identifier: Apache-2.0
# Supply a reviewed Python 3.12 slim image pinned by digest.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN groupadd -g 1000 sandbox && useradd -u 1000 -g 1000 -m sandbox \
    && mkdir -p /var/lib/cognous /opt/cognous/engine \
    && chown 1000:1000 /var/lib/cognous
COPY engine/__init__.py engine/safe_executor.py /opt/cognous/engine/
COPY LICENSE LICENSE-APACHE-2.0 NOTICE /opt/cognous/
COPY engine/LICENSE /opt/cognous/engine/LICENSE
COPY engine/openshell_worker.py /opt/cognous/openshell_worker.py
COPY examples/openshell/probe.py /opt/cognous/probe.py
USER 1000:1000
WORKDIR /var/lib/cognous
CMD ["/bin/sleep", "infinity"]
