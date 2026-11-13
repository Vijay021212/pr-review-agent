"""The offline evaluation: the real agent on test pull requests with planted problems (FR-18).

Each test pull request is a folder (design section 13), reviewed through the real
pipeline with GitHub replaced by the folder, as a dry run, so the evaluation needs no
GitHub access and never posts. The scores follow requirements section 7 and 7.1.
"""
