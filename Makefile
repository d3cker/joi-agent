.PHONY: setup build test test-backend test-macos package package-backend
PYTHON ?= python3
VENV_PYTHON := .venv/bin/python

setup:
	$(PYTHON) -m venv .venv
	$(VENV_PYTHON) -m pip install -e './services/voice-backend[dev]'

build:
	bash apps/macos-client/scripts/build_app.sh

test-backend:
	$(VENV_PYTHON) -m pytest services/voice-backend/tests installer/tests

test-macos:
	bash apps/macos-client/scripts/test.sh

test: test-backend test-macos

package-backend:
	$(PYTHON) scripts/release.py backend

package: test build package-backend
	$(PYTHON) scripts/release.py macos
