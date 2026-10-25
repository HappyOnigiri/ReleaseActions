.PHONY: test

test:
	shellcheck -x actions/publish-release/*.sh actions/prepare-release/*.sh
	python3 -B -m unittest discover -s tests -v
	node --test tests/*.test.mjs
