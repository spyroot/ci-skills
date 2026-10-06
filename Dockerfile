FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

ARG TARGETARCH
ARG NODE_VERSION=v22.11.0
ARG GITLEAKS_VERSION=v8.30.1
ARG TAPLO_VERSION=0.10.0
ARG TAPLO_LINUX_X64_SHA256=8fe196b894ccf9072f98d4e1013a180306e17d244830b03986ee5e8eabeb6156

ENV DEBIAN_FRONTEND=noninteractive
ENV CONDA_DIR=/opt/conda
ENV PATH=/opt/conda/envs/ci-skills/bin:/opt/conda/bin:/usr/local/bin:${PATH}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN test "${TARGETARCH}" = amd64

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      bash bats ca-certificates curl git jq make shellcheck shfmt tar xz-utils gzip && \
    rm -rf /var/lib/apt/lists/*

# Install the Markdown checker in the Ubuntu development image.
RUN curl -fsSL \
      "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" | \
      tar -xJ -C /usr/local --strip-components=1 && \
    npm install -g markdownlint-cli2@0.23.2 && \
    npm cache clean --force

RUN curl -fsSLo /tmp/gitleaks.tar.gz \
      "https://github.com/gitleaks/gitleaks/releases/download/${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION#v}_linux_x64.tar.gz" && \
    tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks && \
    chmod 0755 /usr/local/bin/gitleaks && \
    rm -f /tmp/gitleaks.tar.gz && \
    curl -fsSLo /tmp/taplo.gz \
      "https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-x86_64.gz" && \
    printf '%s  %s\n' "${TAPLO_LINUX_X64_SHA256}" /tmp/taplo.gz | sha256sum -c - && \
    gzip -dc /tmp/taplo.gz >/usr/local/bin/taplo && \
    chmod 0755 /usr/local/bin/taplo && \
    rm -f /tmp/taplo.gz

COPY toolchain-dependencies.json /tmp/toolchain-dependencies.json
RUN conda_url="$(jq -r '.conda.url' /tmp/toolchain-dependencies.json)" && \
    conda_url="${conda_url//\{conda_arch\}/x86_64}" && \
    curl -fsSLo /tmp/miniforge.sh "${conda_url}" && \
    bash /tmp/miniforge.sh -b -p "${CONDA_DIR}" && \
    rm -f /tmp/miniforge.sh

WORKDIR /work
COPY Makefile bless.sh environment.yml toolchain-dependencies.json .markdownlint-cli2.yaml .markdownlint-cli2-version .gitleaks.toml ./
RUN conda env create --file environment.yml
COPY scripts/dev.sh scripts/dev.sh
COPY lib/bash/core/ lib/bash/core/
COPY lib/bash/automation/ lib/bash/automation/
COPY ci-skills/lib/bash/core/runtime.bash ci-skills/lib/bash/core/runtime.bash
COPY schemas/ schemas/
RUN chmod 0755 bless.sh scripts/dev.sh && \
    git init -q && \
    git config user.name ci-skills-smoke && \
    git config user.email ci-skills-smoke@example.invalid && \
    make install && \
    check-jsonschema --schemafile schemas/configuration/toolchain-dependencies.schema.json \
      toolchain-dependencies.json

COPY tests/bash/bless.bats tests/bash/bless.bats
RUN bats --tap tests/bash/bless.bats

CMD ["make", "bless"]
