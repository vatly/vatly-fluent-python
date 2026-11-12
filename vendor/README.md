# Pinned SDK development wheel

`vatly_api_python-0.1.0a2-py3-none-any.whl` is built from the MIT-licensed
`vatly/vatly-api-python` 0.1.0a2 source at commit
`26adc74d8553238bcc94da2f650619de93e9cbd6`. See `SHA256SUMS` for exact wheel bytes.

It lets standalone CI and fresh orbs use the pinned SDK
without a machine-relative sibling checkout. uv's
development source override uses this file; Fluent's wheel only contains
`vatly_fluent` and declares the ordinary `vatly-api-python==0.1.0a2` dependency.
The SDK wheel carries its own MIT license. Source is in the repository above.

The SDK is now [published on PyPI](https://pypi.org/project/vatly-api-python/0.1.0a2/).
This retained development override is not used by ordinary PyPI installations.
To switch development to PyPI, remove the wheel and `[tool.uv.sources]` override,
run `uv lock`, and test index-based installation.
Do not rebuild or replace this version's wheel silently: bump the alpha version
when SDK behavior changes. Keep release tags and their provenance immutable.
