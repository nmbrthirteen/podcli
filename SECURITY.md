# Security policy

## Reporting

Please don't open a public issue for a security problem.

Report it privately through GitHub's [security advisory form](https://github.com/nmbrthirteen/podcli/security/advisories/new),
or email me at [siradze@nikusha.me](mailto:siradze@nikusha.me) with "podcli
security" in the subject.

It helps if you include the version (`podcli --version`), your OS, what an
attacker could do with it, and steps to reproduce.

I'll reply within a few days. When a fix ships I'll credit you in the release
notes, unless you'd rather I didn't.

## Where to look

podcli runs locally and keeps your media on your machine. The parts worth poking
at: the local web UI and its HTTP endpoints, how it handles untrusted media and
transcript files, the bundled binaries and the updater, and how API keys and
config sit on disk.

## Versions

Fixes land on the latest release. Upgrade before reporting, since it might
already be fixed.
