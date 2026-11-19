# Security Policy

## Supported Versions

Security fixes are applied to the latest commit on the `main` branch of **EIDOLON**.

## Security Architecture Notes

EIDOLON executes local desktop actions, voice commands, file operations, and IDE review hooks. Keep the following best practices in mind when running the application:

- **Never commit secrets**: `config/api_keys.json`, `.env`, OAuth tokens, TLS keys/certs, and WhatsApp session folders are ignored via `.gitignore`.
- **Human-in-the-loop confirmations**: Destructive system operations and risky shell commands intercepted by `core/ide_bridge.py` require explicit approval.
- **Local dashboard**: `dashboard/server.py` is designed for local/LAN use with AES-256-CBC application-layer encryption. Do not expose port `8000` directly to the public internet.

## Reporting a Vulnerability

If you discover a security vulnerability in EIDOLON:

1. **Do not** open a public GitHub issue with exploit details.
2. Report it privately via GitHub Security Advisories on [AYMRIKNG/EIDOLON](https://github.com/AYMRIKNG/EIDOLON/security/advisories/new) or contact the maintainer (`@AYMRIKNG`).
3. Include a clear description of the issue, reproduction steps, and potential impact.

You can expect an acknowledgment within 72 hours.
