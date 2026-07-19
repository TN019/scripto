# Contributing to Scripto

Thanks for your interest in Scripto! Contributions — bug reports, fixes, features, docs — are welcome.

> 中文读者：贡献前请阅读下方「Contribution terms」一节。为保证再许可条款在法律上明确，该节以英文为准。

## Getting started

```bash
git clone https://github.com/TN019/scripto.git && cd scripto
uv sync
uv run pytest          # fast suite must stay green
```

- Keep the fast test suite passing (`uv run pytest`). Add tests for behavior you change.
- Match the existing style; the `core/` layer must never import UI code.
- Real-engine / Ollama smokes are opt-in: `SCRIPTO_ENGINE_SMOKE=1` and `SCRIPTO_OLLAMA_SMOKE=1`.
- Open a pull request against `main` with a clear description of the problem and the change.

## Reporting bugs

Open an issue with: what you did, what you expected, what happened, your OS, and the output of `uv run scripto-cli doctor`.

## Contribution terms (please read)

By submitting a contribution (a pull request, patch, or any other material) to this
project, you represent and agree that:

1. **You have the right to submit it.** The contribution is your own original work, or
   you otherwise have the right to submit it under these terms.

2. **License to the project and its users.** You license your contribution under the
   MIT License — the project's current license — to the project and to everyone who
   receives the project.

3. **Relicensing grant to the maintainer.** You additionally grant the project's
   maintainer(s) a perpetual, worldwide, non-exclusive, royalty-free, irrevocable
   right to use, reproduce, modify, prepare derivative works of, sublicense, and
   **relicense** your contribution under any license terms — including proprietary or
   commercial terms — as part of this project or any derivative of it, without further
   notice, permission, or compensation.

This lets the maintainer keep the project open source today while preserving the option
to offer it (or a derivative) under different terms in the future. If you do not agree
to these terms, please do not submit a contribution.

> These terms are a plain-language license grant, not legal advice. For anything
> significant, consult a lawyer.
