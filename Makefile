.DEFAULT_GOAL := check
.PHONY: check test

check test:
	bash scripts/check.sh
