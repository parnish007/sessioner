# Third-party notices

Sessioner's terminal interface is in `src/sessioner`. Its account engine is a local fork of [claude-swap](https://github.com/realiti4/claude-swap), created by Onur Cetinkol and upstream contributors.

The engine is included under the original MIT license, copyright 2026 Onur Cetinkol. Its full permission and copyright notice are preserved in [vendor/claude-swap/LICENSE](vendor/claude-swap/LICENSE). The checkout is based on upstream commit `3a4e5c14873eb5b32f182d55c68da98ac8c0db45`.

Sessioner adds its product CLI, account-only quota hook, launch helpers, and documentation. The bundled account component remains credited to its authors. See [licenses](docs/credits.md) for the applicable notices.

Other installed Python dependencies retain their own licenses and notices. Sessioner is an independent project and does not imply endorsement by Anthropic or the upstream engine authors.
