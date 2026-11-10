# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem.

- Use GitHub's private vulnerability reporting: Security tab → "Report a
  vulnerability".
- Say what you found, how to reproduce it, and what it lets an attacker do.
- I will confirm receipt within 7 days.
- I will publish a fix and an advisory as soon as the fix is ready.

## Supported versions

Only the latest release gets security fixes.

## Scope

Anything in this repository, in particular these:

- a weather service's response can harm Home Assistant: code execution,
  crashes, memory or CPU exhaustion, a request to another host;
- the home location leaks beyond what [docs/privacy.md](docs/privacy.md)
  documents, for example in logs, diagnostics, attributes or requests;
- Bellweather gives anyone a way into Home Assistant.

How Bellweather is built to prevent these is described in
[docs/security.md](docs/security.md).
