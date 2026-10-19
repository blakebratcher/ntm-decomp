# Public ntm-decomp tree: documentation, classification metadata and tooling.
# The recovered source corpus is private and is not distributed.
NTMD_ROM ?= /path/to/turfmast.zip
.DEFAULT_GOAL := help

.PHONY: help setup verify-rom extract test audit check clean private-only

help:
	@echo 'Public targets:'
	@echo '  make setup       fetch and build pinned vasm 2.0f'
	@echo '  make test        run the corpus-independent test suite'
	@echo '  make check       audit the public tree, then run the tests'
	@echo '  make verify-rom  verify your own 200- ROM dump (NTMD_ROM=...)'
	@echo '  make audit       enforce the public-tree allowlist and leak checks'
	@echo '  make clean       remove build/'
	@echo 'Targets that need the private corpus (build, m1, m1-check, rom,'
	@echo 'mame-check, ...) print a clear error.'

setup:
	tools/setup_vasm.sh

verify-rom:
	@case '$(NTMD_ROM)' in ''|/path/to/turfmast.zip) echo 'error: set NTMD_ROM=/path/to/turfmast.zip' >&2; exit 2;; esac
	python3 tools/extract.py --roms '$(NTMD_ROM)'

extract: verify-rom

test: setup
	python3 -m pytest tests/ -q

audit:
	python3 tools/audit_public.py

check: audit test

private-only:
	@echo 'error: $@ needs the private source corpus, which is not distributed.' >&2
	@echo 'This public tree ships documentation, metadata and corpus-independent tooling.' >&2
	@exit 2

build m1 m1-check rom mame-check source-check completion-check symbolize progress c-check import disasm-m1: private-only

clean:
	rm -rf build
