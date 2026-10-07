SHELL := /bin/bash
export PATH := /opt/homebrew/bin:/opt/homebrew/sbin:$(PATH)
.DEFAULT_GOAL := help

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
CONDA_ENV ?= ci-skills
CONDA ?= $(shell command -v conda || printf '%s' "$$HOME/miniconda3/condabin/conda")
DOCKER ?= docker
DOCKER_IMAGE ?= ci-skills:dev
DOCKER_PLATFORM ?= linux/amd64

.PHONY: help install toolchain conda install-hooks bless bless-all pretty \
	pretty-markdown pretty-python pretty-shell build docker-build docker-smoke docker

help:
	@printf '%s\n' \
		'install           Install declared hook tools, the conda environment, and the pre-commit hook.' \
		'toolchain         Install missing host tools in the bless profile.' \
		'conda             Create or update the ci-skills environment from environment.yml.' \
		'install-hooks     Install the repository hook without overriding global hooks.' \
		'bless             Check exact staged index content; this is the commit hook command.' \
		'bless-all         Check tracked working-tree files without rewriting them.' \
		'pretty            Format tracked Markdown, Python, and Bash files; never stage them.' \
		'build             Build the Ubuntu Linux development image.' \
		'docker             Run the Linux hook smoke built into the image.'

install:
	@fingerprint=$$($(ROOT)/scripts/dev.sh install --dry-run --json | jq -er .plan_fingerprint) && \
		$(ROOT)/scripts/dev.sh install --apply --confirm-install "$$fingerprint"

toolchain:
	@fingerprint=$$($(ROOT)/scripts/dev.sh toolchain --dry-run --json | jq -er .plan_fingerprint) && \
		$(ROOT)/scripts/dev.sh toolchain --apply --confirm-install "$$fingerprint"

conda:
	@fingerprint=$$($(ROOT)/scripts/dev.sh conda --dry-run --json | jq -er .plan_fingerprint) && \
		$(ROOT)/scripts/dev.sh conda --apply --confirm-install "$$fingerprint"

install-hooks:
	@fingerprint=$$($(ROOT)/scripts/dev.sh hooks --dry-run --json | jq -er .plan_fingerprint) && \
		$(ROOT)/scripts/dev.sh hooks --apply --confirm-install "$$fingerprint"

bless:
	@$(ROOT)/bless.sh --staged

bless-all:
	@$(ROOT)/bless.sh --all

pretty: pretty-markdown pretty-python pretty-shell

pretty-markdown:
	@markdownlint-cli2 --fix '**/*.md'

pretty-python:
	@$(CONDA) run -n $(CONDA_ENV) ruff check --fix .
	@$(CONDA) run -n $(CONDA_ENV) ruff format .

pretty-shell:
	@shopt -s nullglob; shfmt -i 2 -w bless.sh scripts/*.sh scripts/bash/core/*.bash lib/bash/core/*.bash lib/bash/automation/*.bash ci-skills/lib/bash

build: docker-build

docker-build:
	@$(DOCKER) build --platform $(DOCKER_PLATFORM) --tag $(DOCKER_IMAGE) .

docker-smoke: docker-build
	@$(DOCKER) run --rm --platform $(DOCKER_PLATFORM) $(DOCKER_IMAGE)

docker: docker-smoke
