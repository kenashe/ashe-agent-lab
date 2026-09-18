# Ashe Agent Lab - common tasks.
#
# These targets are the canonical commands. If you change how something is run,
# change it here, and the README and AGENTS.md will keep pointing at the truth.
#
# Everything below works with no credentials and no network: the default
# experiment uses the offline `echo` fixture provider.

PYTHON ?= python3
EXPERIMENT ?= fact-check-awareness-001

export PYTHONPATH := src

.PHONY: help
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install:  ## Install the package and dev dependencies in editable mode
	$(PYTHON) -m pip install -e ".[dev]"

.PHONY: test
test:  ## Run the full test suite (offline, no credentials needed)
	$(PYTHON) -m pytest

.PHONY: test-verbose
test-verbose:  ## Run the test suite with per-test output
	$(PYTHON) -m pytest -v

.PHONY: validate
validate:  ## Validate every experiment definition without calling any model
	$(PYTHON) -m ashe_lab validate --all

.PHONY: demo
demo:  ## Run the example experiment offline, then show its results
	$(PYTHON) -m ashe_lab run $(EXPERIMENT)
	@echo ""
	$(PYTHON) -m ashe_lab show $(EXPERIMENT)

.PHONY: dry-run
dry-run:  ## Print every rendered prompt for the example experiment, call nothing
	$(PYTHON) -m ashe_lab run $(EXPERIMENT) --dry-run

.PHONY: report
report:  ## Regenerate the report for the most recent run of EXPERIMENT
	$(PYTHON) -m ashe_lab report $(EXPERIMENT)

.PHONY: verify
verify:  ## Check the integrity of the most recent run of EXPERIMENT
	$(PYTHON) -m ashe_lab verify $(EXPERIMENT)

.PHONY: runs
runs:  ## List every stored run
	$(PYTHON) -m ashe_lab list runs

.PHONY: check
check: validate test  ## Validate experiments and run the test suite

.PHONY: clean
clean:  ## Remove caches and build artifacts. Does NOT touch runs/
	rm -rf .pytest_cache build dist src/*.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.py[co]' -delete

.PHONY: clean-runs
clean-runs:  ## DESTRUCTIVE: delete all stored run evidence. Asks first.
	@echo "This permanently deletes every run directory under runs/."
	@echo "Run directories are experimental evidence and cannot be regenerated."
	@read -p "Type 'delete' to confirm: " answer; \
	if [ "$$answer" = "delete" ]; then \
		chmod -R u+w runs 2>/dev/null || true; \
		find runs -mindepth 1 -maxdepth 1 ! -name '.gitkeep' -exec rm -rf {} +; \
		echo "runs/ emptied."; \
	else \
		echo "Aborted. Nothing was deleted."; \
	fi
