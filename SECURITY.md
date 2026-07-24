# Security Policy

## Supported Versions

MandiIQ is a portfolio/learning project. Security fixes are applied to the
latest `master` branch only.

| Version | Supported |
| ------- | --------- |
| master  | ✅        |
| older   | ❌        |

## Reporting a Vulnerability

If you discover a security vulnerability in MandiIQ, please **do not** open a
public issue. Instead, report it privately:

- **Email:** siba@unifies.codes (replace with your real contact)
- Or use GitHub's private vulnerability reporting (if enabled on the repo)

Please include:

- A description of the vulnerability and its impact
- Steps to reproduce (or a proof-of-concept)
- Any suggested mitigation, if known

You can expect an acknowledgement within a few days. Once the issue is
confirmed and fixed, we will coordinate a disclosure timeline with you.

## Data & Privacy Notes

- MandiIQ pulls public agricultural price/weather data (Agmarknet, data.gov.in,
  IMD). It does not collect or store any personal user data.
- API keys (OpenRouter, data.gov.in, Gemini) live only in Streamlit Cloud
  secrets / local `.env` and are never committed to the repository.
