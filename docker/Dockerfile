# Online dependency bootstrap; product execution is network-denied.
FROM python:3.14.0-slim-bookworm@sha256:9bbb8720ae0a24a6ca8dd678bfdf57818fe70caf54c52a53ec01d8db43405056
ARG HERMES_REF=v0.22.5
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates bash && rm -rf /var/lib/apt/lists/*
RUN case "$HERMES_REF" in v0.22.2|v0.22.5) ;; *) exit 2 ;; esac && \
    git clone --depth 1 --branch "$HERMES_REF" https://github.com/NousResearch/hermes-agent.git /opt/hermes && \
    git config --system --add safe.directory /opt/hermes
COPY requirements-test.txt /opt/requirements-test.txt
ENV HOME=/opt/runtime-build-home HERMES_HOME=/opt/runtime-build-home/.hermes
RUN PYTHONPATH=/opt/hermes python -m pm.build_env --source /opt/hermes --cache /opt/pm-cache --export-requirements /opt/core-requirements.txt && \
    PYTHONPATH=/opt/hermes python -m pm.build_env --source /opt/hermes --out /opt/runtime --cache /opt/pm-cache --no-install-project && \
    python -m pip --python /opt/runtime install --no-cache-dir -c /opt/core-requirements.txt -r /opt/requirements-test.txt && \
    chmod -R a+rX /opt/hermes /opt/runtime /opt/runtime-build-home
ENV PATH=/opt/runtime/bin:$PATH PYTHONPATH=/opt/hermes HOME=/scratch/home HERMES_HOME=/scratch/home/.hermes TMPDIR=/scratch PYTHONDONTWRITEBYTECODE=1 HERMES_AGENT_ROOT=/opt/hermes
COPY plugin.yaml __init__.py /opt/fixture/home/plugins/kanban-delivery/
COPY delivery/*.py /opt/fixture/home/plugins/kanban-delivery/delivery/
# Full native admission is online setup, not a mocked enabled configuration.
RUN mkdir -p /scratch /opt/fixture/user && \
    HOME=/opt/fixture/user HERMES_HOME=/opt/fixture/home HERMES_RUNTIME_DIR=/opt/fixture/tools \
    python -m hermes_cli.main plugins enable kanban-delivery && \
    HOME=/opt/fixture/user HERMES_HOME=/opt/fixture/home HERMES_RUNTIME_DIR=/opt/fixture/tools \
    python -m hermes_cli.main plugins list && \
    chmod -R a+rX /opt/fixture
USER 65532:65532
WORKDIR /source
CMD ["bash", "scripts/verify.sh"]
