.PHONY: clean compile_translations coverage diff_cover docs dummy_translations \
        extract_translations fake_translations help pii_check pull_translations push_translations \
        quality requirements selfcheck test test-all upgrade validate

.DEFAULT_GOAL := help

# For opening files in a browser. Use like: $(BROWSER)relative/path/to/file.html
BROWSER := python -m webbrowser file://$(CURDIR)/

help: ## display this help message
	@echo "Please use \`make <target>' where <target> is one of"
	@perl -nle'print $& if m{^[a-zA-Z_-]+:.*?## .*$$}' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m  %-25s\033[0m %s\n", $$1, $$2}'

clean: ## remove generated byte code, coverage reports, and build artifacts
	find . -name '__pycache__' -exec rm -rf {} +
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	coverage erase
	rm -fr build/
	rm -fr dist/
	rm -fr *.egg-info

coverage: clean ## generate and view HTML coverage report
	PYTHONPATH=./:./mock_apps pytest --cov-report html
	$(BROWSER)htmlcov/index.html

docs: ## generate Sphinx HTML documentation, including API docs
	tox -e docs
	$(BROWSER)docs/_build/html/index.html

upgrade: export CUSTOM_COMPILE_COMMAND=make upgrade
upgrade: ## update the requirements/*.txt files with the latest packages satisfying requirements/*.in
	pip install -qr requirements/pip-tools.txt
	# Make sure to compile files after any other files they include!
	pip-compile --upgrade -o requirements/pip-tools.txt requirements/pip-tools.in
	pip-compile --upgrade -o requirements/base.txt requirements/base.in
	pip-compile --upgrade -o requirements/test.txt requirements/test.in
	pip-compile --upgrade -o requirements/doc.txt requirements/doc.in
	pip-compile --upgrade -o requirements/quality.txt requirements/quality.in
	pip-compile --upgrade -o requirements/ci.txt requirements/ci.in
	pip-compile --upgrade -o requirements/pii_check.txt requirements/pii_check.in
	pip-compile --upgrade -o requirements/dev.txt requirements/dev.in
	# Let tox control the Django,celery versions for tests
	sed '/^[dD]jango==/d' requirements/test.txt > requirements/test.tmp
	mv requirements/test.tmp requirements/test.txt
	grep -e "^amqp==\|^anyjson==\|^billiard==\|^celery==\|^kombu==\|^click-didyoumean==\|^click-repl==\|^click==\|^prompt-toolkit==\|^vine==" requirements/base.txt > requirements/celery50.txt
	sed -i.tmp '/^amqp==/d' requirements/test.txt
	sed -i.tmp '/^anyjson==/d' requirements/test.txt
	sed -i.tmp '/^billiard==/d' requirements/test.txt
	sed -i.tmp '/^celery==/d' requirements/test.txt
	sed -i.tmp '/^kombu==/d' requirements/test.txt
	sed -i.tmp '/^vine==/d' requirements/test.txt
	rm requirements/*.txt.tmp

quality: ## check coding style with pycodestyle and pylint
	tox -e quality

pii_check: ## check for PII annotations on all Django models
	tox -e pii_check

requirements: ## install development environment requirements
	pip install -qr requirements/pip-tools.txt
	pip-sync requirements/dev.txt requirements/private.*

test: clean ## run tests in the current virtualenv
	PYTHONPATH=./:./mock_apps pytest

diff_cover: test ## find diff lines that need test coverage
	diff-cover coverage.xml

test-all: quality pii_check ## run tests on every supported Python/Django combination
	tox

validate: quality pii_check test ## run tests and quality checks

selfcheck: ## check that the Makefile is well-formed
	@echo "The Makefile is well-formed."

## Localization targets

extract_translations: ## extract strings to be translated, outputting .mo files
	rm -rf docs/_build
	cd edx-bulk-grades && ../manage.py makemessages -l en -v1 -d django
	cd edx-bulk-grades && ../manage.py makemessages -l en -v1 -d djangojs

compile_translations: ## compile translation files, outputting .po files for each supported language
	cd edx-bulk-grades && ../manage.py compilemessages

detect_changed_source_translations:
	cd edx-bulk-grades && i18n_tool changed

pull_translations: ## pull translations from Transifex
	tx pull -af --mode reviewed

push_translations: ## push source translation files (.po) from Transifex
	tx push -s

dummy_translations: ## generate dummy translation (.po) files
	cd bulk_grades && i18n_tool dummy

build_dummy_translations: extract_translations dummy_translations compile_translations ## generate and compile dummy translation files

validate_translations: build_dummy_translations detect_changed_source_translations ## validate translations

##################
#Devstack commands
##################

install-local: ## installs your local bulk-grades code into the LMS virtualenv
	docker exec -t edx.devstack.lms bash -c '. /edx/app/edxapp/venvs/edxapp/bin/activate && cd /edx/app/edxapp/edx-platform && pip uninstall -y edx-bulk-grades && pip install -e /edx/src/edx-bulk-grades && pip freeze | grep edx-bulk-grades'
