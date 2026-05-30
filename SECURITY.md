# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in agentcloak, please report it responsibly.

**Email:** [lincmars@gmail.com](mailto:lincmars@gmail.com)

Include:

- A description of the vulnerability
- Steps to reproduce
- Affected versions (if known)
- Any potential impact assessment

## Response Timeline

- **Acknowledgment:** within 48 hours
- **Initial assessment:** within 7 days
- **Fix or mitigation:** depends on severity, typically within 30 days

## Scope

The following are in scope:

- agentcloak daemon (HTTP API, browser management)
- CLI input handling and output sanitization
- MCP server tool implementations
- IDPI security layer (domain whitelist/blacklist, content scanning)
- RemoteBridge WebSocket authentication
- Browser profile storage and cookie handling

The following are out of scope:

- Vulnerabilities in upstream dependencies (CloakBrowser, Playwright, aiohttp) -- report those to the respective projects
- Bot-detection classification of automated traffic -- agentcloak presents a realistic browser environment by design; detection outcomes are not security vulnerabilities
- Issues requiring physical access to the machine running agentcloak

## Supported Versions

| Version | Supported           |
|---------|---------------------|
| 0.3.x   | Yes                 |
| 0.2.x   | Security fixes only |
| < 0.2   | No                  |

## Disclosure

We follow coordinated disclosure. Please do not open a public issue for security vulnerabilities. We will credit reporters in the release notes unless anonymity is requested.
