# Pre-publication SDK bootstrap

`vatly_sdk-0.1.0a1-py3-none-any.whl` is built from the MIT-licensed
`vatly/vatly-api-python` commit `4be4eaf30bd708707474eb5f3549ac4cb285fd7d`.
SHA-256: `6feb18368c0f65cd11e64512dcb555d58c6a6cc65ef3f34e9c828f246d71ed45`.

It lets standalone private-repository CI and fresh orbs use the unpublished SDK
without a personal GitHub token or a machine-relative sibling checkout. uv's
development source override uses this file; Fluent's wheel only contains
`vatly_fluent` and declares the ordinary `vatly-sdk==0.1.0a1` dependency.
The SDK wheel carries its own MIT license. Source is in the repository above.

Once the SDK is published, remove this wheel and the `[tool.uv.sources]` override,
run `uv lock`, and test index-based installation before releasing Fluent.
Do not rebuild or replace this version's wheel silently: bump the alpha version
when SDK behavior changes. No PyPI publication or Git tag has been made.
