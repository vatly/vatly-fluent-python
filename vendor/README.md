# Pre-publication SDK bootstrap

`vatly_api_python-0.1.0a2-py3-none-any.whl` is built from the MIT-licensed
`vatly/vatly-api-python` 0.1.0a2 source at commit
`26adc74d8553238bcc94da2f650619de93e9cbd6`. See `SHA256SUMS` for exact wheel bytes.

It lets standalone private-repository CI and fresh orbs use the unpublished SDK
without a personal GitHub token or a machine-relative sibling checkout. uv's
development source override uses this file; Fluent's wheel only contains
`vatly_fluent` and declares the ordinary `vatly-api-python==0.1.0a2` dependency.
The SDK wheel carries its own MIT license. Source is in the repository above.

Once the SDK is published, remove this wheel and the `[tool.uv.sources]` override,
run `uv lock`, and test index-based installation before releasing Fluent.
Do not rebuild or replace this version's wheel silently: bump the alpha version
when SDK behavior changes. No PyPI publication or Git tag has been made.
