# This Makefile was copied from another repository and is not adapted yet.
# current todo adopt and integrate , so I need move required scripts and adjust
#
# Mustafa Bayramov mbayramo@cisco.com

SHELL := /bin/bash
export PATH := /opt/homebrew/bin:/opt/homebrew/sbin:$(PATH)
REPO_ROOT := $(CURDIR)
.DEFAULT_GOAL := bless
TOOLCHAIN_SCRIPT := scripts/toolchain/install.sh
CONDA_INSTALL_SCRIPT := scripts/toolchain/conda.sh
TOOLCHAIN_MANIFEST := $(REPO_ROOT)/toolchain-dependencies.json
TOOLCHAIN_PREFIX ?=
TOOLCHAIN_TIMEOUT ?= 1800

TOOLBOX_SCRIPT := scripts/toolbox/toolbox-image.sh
TOOLBOX_AUTH_SCRIPT := scripts/toolbox/toolbox-auth-smoke.sh
TOOLBOX_CONTEXT_FILES := environment.yml \
	platforms/component/toolbox/Containerfile \
	platforms/component/toolbox/toolbox.yaml
K8S_TEST_SCRIPT := scripts/ci/k8s-test.sh
# MAX_JOBS is caller-selected; JOBS is the effective local CPU count.
# Preserve JOBS as a legacy way to supply the requested maximum.
REQUESTED_JOBS := $(JOBS)
MAX_JOBS ?= $(if $(REQUESTED_JOBS),$(REQUESTED_JOBS),4)
# The job-count library belongs to the toolchain targets, which this repository
# does not have yet; without it the requested maximum is used as is.
JOBS_LIBRARY := $(REPO_ROOT)/lib/bash/toolchain/jobs.bash
override JOBS := $(if $(wildcard $(JOBS_LIBRARY)),$(shell bash -c \
	'source "$$1"; CI_toolchain_job_count "$$2"' _ "$(JOBS_LIBRARY)" \
	"$(MAX_JOBS)"),$(MAX_JOBS))
export MAX_JOBS JOBS
ifeq ($(filter -j% --jobs%,$(MAKEFLAGS)),)
MAKEFLAGS += --jobs=$(JOBS)
endif
TEST ?= bats --jobs $(JOBS) tests
CONDA_ENV ?= ci-skills
XARGS ?= xargs

.NOTPARALLEL: bless install install-bless install-hooks toolchain \
	toolchain-dry-run conda conda-dry-run

.PHONY: bless install install-bless install-hooks toolchain toolchain-dry-run \
	conda conda-dry-run pretty pretty-markdown pretty-python pretty-shell help \
	k8s-test k8s-test-dry-run toolbox toolbox-auth toolbox-dry-run

bless: install-bless
	@cd "$(REPO_ROOT)" && ./bless.sh --staged

install-bless: install-hooks

# .githooks/pre-commit (tracked) calls ./bless.sh --staged before every commit.
install-hooks:
	@test -x "$(REPO_ROOT)/.githooks/pre-commit"
	@git -C "$(REPO_ROOT)" config --local core.hooksPath .githooks

install: install-bless $(if $(filter conda conda-dry-run,$(MAKECMDGOALS)),,toolchain)

toolchain:
	$(TOOLCHAIN_SCRIPT) --manifest "$(TOOLCHAIN_MANIFEST)" \
		$(if $(TOOLCHAIN_PREFIX),--prefix "$(TOOLCHAIN_PREFIX)") \
		--timeout "$(TOOLCHAIN_TIMEOUT)" --apply --confirm-install --json

toolchain-dry-run:
	$(TOOLCHAIN_SCRIPT) --manifest "$(TOOLCHAIN_MANIFEST)" \
		$(if $(TOOLCHAIN_PREFIX),--prefix "$(TOOLCHAIN_PREFIX)") \
		--timeout "$(TOOLCHAIN_TIMEOUT)" --dry-run --json

conda:
	$(CONDA_INSTALL_SCRIPT) --manifest "$(TOOLCHAIN_MANIFEST)" \
		--timeout "$(TOOLCHAIN_TIMEOUT)" --apply --confirm-install --json

conda-dry-run:
	$(CONDA_INSTALL_SCRIPT) --manifest "$(TOOLCHAIN_MANIFEST)" \
		--timeout "$(TOOLCHAIN_TIMEOUT)" --dry-run --json

pretty: pretty-markdown pretty-python pretty-shell

pretty-markdown:
	@set -Eeuo pipefail; \
	source "$(REPO_ROOT)/automation/lib/core/exit_codes.bash"; \
	source "$(REPO_ROOT)/lib/bash/toolchain/pretty.bash"; \
	inventory="$$(mktemp -t ci-skills-pretty-markdown.XXXXXX)"; \
	trap 'rm -f -- "$$inventory"' EXIT; \
	CI_toolchain_changed_files "$(REPO_ROOT)" "$$inventory" \
		'*.md' '*.markdown'; \
	if [[ ! -s "$$inventory" ]]; then \
		printf '%s\n' 'Markdown pretty: not_applicable'; \
		exit 0; \
	fi; \
	if ! command -v markdownlint-cli2 >/dev/null || \
		! command -v "$(XARGS)" >/dev/null; then \
		printf '%s\n' 'BLOCKER: Markdown pretty tools are missing.' \
			'SAFE_NEXT_STEP: run make install toolchain, then rerun make pretty.' >&2; \
		exit "$$CI_EXIT_BLOCKED"; \
	fi; \
	cd "$(REPO_ROOT)"; \
	$(XARGS) -0 markdownlint-cli2 --config .markdownlint-cli2.yaml --fix -- \
		<"$$inventory"

pretty-python:
	@set -Eeuo pipefail; \
	source "$(REPO_ROOT)/automation/lib/core/exit_codes.bash"; \
	source "$(REPO_ROOT)/lib/bash/toolchain/pretty.bash"; \
	inventory="$$(mktemp -t ci-pretty-python.XXXXXX)"; \
	trap 'rm -f -- "$$inventory"' EXIT; \
	CI_toolchain_changed_files "$(REPO_ROOT)" "$$inventory" '*.py'; \
	if [[ ! -s "$$inventory" ]]; then \
		printf '%s\n' 'Python pretty: not_applicable'; \
		exit 0; \
	fi; \
	conda_bin="$$(bash -c 'source "$$1"; CI_find_conda' \
		_ "$(REPO_ROOT)/automation/lib/core/conda.bash")"; \
	if [[ -z "$$conda_bin" ]]; then \
		printf '%s\n' 'BLOCKER: Python pretty requires Conda.' \
			'SAFE_NEXT_STEP: run make install conda, then rerun make pretty.' >&2; \
		exit "$$CI_EXIT_BLOCKED"; \
	fi; \
	if ! command -v "$(XARGS)" >/dev/null || \
		! "$$conda_bin" run -n "$(CONDA_ENV)" ruff --version >/dev/null; then \
		printf '%s\n' 'BLOCKER: Python pretty tools are missing.' \
			'SAFE_NEXT_STEP: run make install conda, then rerun make pretty.' >&2; \
		exit "$$CI_EXIT_BLOCKED"; \
	fi; \
	cd "$(REPO_ROOT)"; \
	rc=0; \
	$(XARGS) -0 "$$conda_bin" run -n "$(CONDA_ENV)" ruff check \
		--fix --no-cache -- <"$$inventory" || rc=$$?; \
	$(XARGS) -0 "$$conda_bin" run -n "$(CONDA_ENV)" ruff format \
		--no-cache -- <"$$inventory" || rc=$$?; \
	exit "$$rc"

pretty-shell:
	@set -Eeuo pipefail; \
	source "$(REPO_ROOT)/automation/lib/core/exit_codes.bash"; \
	source "$(REPO_ROOT)/lib/bash/toolchain/pretty.bash"; \
	inventory="$$(mktemp -t ci-pretty-shell.XXXXXX)"; \
	trap 'rm -f -- "$$inventory"' EXIT; \
	CI_toolchain_changed_files "$(REPO_ROOT)" "$$inventory" \
		'*.sh' '*.bash'; \
	if [[ ! -s "$$inventory" ]]; then \
		printf '%s\n' 'Shell pretty: not_applicable'; \
		exit 0; \
	fi; \
	if ! command -v shfmt >/dev/null || \
		! command -v "$(XARGS)" >/dev/null; then \
		printf '%s\n' 'BLOCKER: Shell pretty tools are missing.' \
			'SAFE_NEXT_STEP: run make install toolchain, then rerun make pretty.' >&2; \
		exit "$$CI_EXIT_BLOCKED"; \
	fi; \
	cd "$(REPO_ROOT)"; \
	$(XARGS) -0 shfmt -w -- <"$$inventory"

help:
	@printf '%s\n' \
		'Targets:' \
		'  bless            Install the pre-commit hook and bless staged changes (default).' \
		'  install-bless    Install the pre-commit hook that runs bless.sh.' \
		'  install-hooks    Activate .githooks/pre-commit (core.hooksPath .githooks).' \
		'  install          Install hooks and general tools; conda stays separate.' \
		'  toolchain        Install general tools from the JSON manifest.' \
		'  toolchain-dry-run Show the dependency installation plan as JSON.' \
		'  conda            Install Conda and synchronize environment.yml.' \
		'  conda-dry-run    Show the separate Conda installation plan.' \
		'  pretty           Format changed Markdown, Python, and shell; do not stage.' \
		'  k8s-test         Run TEST in Kubernetes using the Harbor toolbox.' \
		'  k8s-test-dry-run Show the Kubernetes test Job plan as JSON.' \
		'  toolbox          Build the Ubuntu toolbox in OpenShift, push to Harbor, read back.' \
		'  toolbox-auth     Smoke Helm, GCR, and Harbor auth in toolbox.' \
		'  toolbox-dry-run  Show the toolbox action plan as JSON.' \
		'' \
		"Examples:" \
		"  make" \
		"  make install" \
		"  make install toolchain" \
		"  make install conda" \
		"  make pretty" \
		"  make toolbox" \
		"  make toolbox-auth" \
		"  make k8s-test TEST='bats tests/toolbox_image.bats'"

k8s-test:
	CI_K8S_TEST_COMMAND='$(TEST)' $(K8S_TEST_SCRIPT) --apply --json

k8s-test-dry-run:
	CI_K8S_TEST_COMMAND='$(TEST)' $(K8S_TEST_SCRIPT) --json

toolbox: $(TOOLBOX_CONTEXT_FILES)
	$(TOOLBOX_SCRIPT) --apply --json

toolbox-auth:
	$(TOOLBOX_AUTH_SCRIPT) --apply --json

toolbox-dry-run: $(TOOLBOX_CONTEXT_FILES)
	$(TOOLBOX_SCRIPT) --json
