.DEFAULT_GOAL := help

.PHONY: help bless lint verify-bless install-hooks

help:
	@printf '%s\n' \
		'  make bless          Check staged files before a commit.' \
		'  make lint           Check tracked and untracked, non-ignored files.' \
		'  make verify-bless   Run the offline hook fixture tests.' \
		'  make install-hooks  Activate the repository pre-commit hook locally.'

bless:
	@./bless.sh --staged

lint:
	@./bless.sh --all

verify-bless:
	@bats tests/bash/bless.bats

install-hooks:
	@test -x .githooks/pre-commit
	@git config --local core.hooksPath .githooks
