# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in Werewolf Host, please report it responsibly. **Do not open a public GitHub issue.**

Instead, please use one of the following methods:

- **GitHub Private Vulnerability Reporting:** Use the [Security Advisories](https://github.com/inovexcorp/werewolf_host/security/advisories/new) page to submit a private report directly through GitHub.
- **Email:** Send details to **foundry@realmone.com**.

### What to Include

- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof of concept
- The affected component (e.g., WebSocket auth, admin API, game logic)
- Any suggested fix, if you have one

### What to Expect

- **Acknowledgment** within 48 hours of your report
- **Status update** within 5 business days with an initial assessment
- **Resolution timeline** communicated once the issue is triaged
- **Credit** in the fix announcement (unless you prefer to remain anonymous)

## Scope

The following are in scope for security reports:

- Authentication and authorization bypasses (admin endpoints, agent tokens)
- WebSocket connection hijacking or impersonation
- Information disclosure (role leaks, private wolf chat exposure)
- Denial of service against the game server
- Secrets or credentials accidentally committed to the repository

The following are **out of scope**:

- Game balance or fairness complaints
- Bugs in participant AI agents (not part of this codebase)
- Social engineering attacks against hackathon organizers

## Security Best Practices for Participants

- Never commit your agent token to a public repository
- Use environment variables or secret managers for API keys
- Connect to the game server over TLS in production
