.PHONY: test

test:
	shellcheck -x actions/publish-release/*.sh
	python3 -B -m unittest discover -s tests -v
