# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report privately through GitHub's **"Report a vulnerability"** button under the
repository's **Security** tab
([Security Advisories](https://github.com/TN019/scripto/security/advisories/new)).
Include what you found, how to reproduce it, and the potential impact. You'll get a
response as soon as reasonably possible.

## Scope

Scripto runs entirely on your own machine and performs no network calls of its own
beyond:

- downloading Whisper models from Hugging Face (only when you choose to download one), and
- talking to a **local** Ollama instance for translation (only when you enable it).

It uploads nothing and opens no listening ports. The most relevant security surface is
therefore local: handling of file paths, subprocess invocation (`ffmpeg`), and the
files Scripto reads and writes. Reports in these areas are especially welcome.

## Supported versions

Fixes land on `main` and in the latest release. There is no long-term support branch.
