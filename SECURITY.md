# Security policy

## Supported versions

Security fixes are applied to the latest released minor version.

| Version | Supported |
|---|---|
| 2.1.x | Yes |
| Earlier versions | No |

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** button on this repository's Security page. This
creates a private security advisory visible only to the reporter and maintainers. Do not publish
exploit details, private filesystem paths, snapshots, or diagnostic archives in a public issue.

If private vulnerability reporting is unavailable, open a public issue containing only a request
for a private contact channel. Do not include the vulnerability details in that issue.

Useful private reports include:

- the affected KingDirSTAT version and operating system;
- a minimal reproduction using synthetic paths and files;
- the expected and actual behavior;
- the security impact; and
- any suggested mitigation, if known.

Relevant security boundaries include symlink/reparse handling, output-path validation, generated
cleanup scripts, spreadsheet-formula injection, unsafe deserialization, and unintended disclosure
of filesystem paths. A snapshot containing paths is sensitive data, but the intentional inclusion
of scanned names in a user-requested report is not by itself a vulnerability.
See the [path privacy guidance](README.md#path-privacy) before sharing any snapshot or report.

Maintainers will acknowledge a complete report as soon as practical, investigate it privately,
and coordinate disclosure and credit with the reporter. Please allow time for a fix and release
before public disclosure.
