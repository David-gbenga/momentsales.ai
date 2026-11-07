# Compliance documents

Planned deliverables (PRD *Security, privacy and compliance*): DPIA template, record of processing activities, privacy policy, SaaS terms, DPA and sub-processor list, acceptable-use policy. Drafted in Phase 4 (marketing site and legal pages) and reviewed by a lawyer before launch.

## Phase 0 personal data (for the record of processing)
| Data | Where | Purpose | Lawful basis | Retention |
| --- | --- | --- | --- | --- |
| Waitlist email, consent version and time, IP HMAC | `waitlist_signups` | Launch and early-access emails | Consent (unbundled tick box, double opt-in) | Until unsubscribe or launch + 12 months (to confirm) |
| User email, name, Google `sub` | `users` | Accounts and sign-in | Contract | Account lifetime |
| Sign-in IP HMAC, user agent | `refresh_tokens`, `magic_link_tokens`, `audit_log` | Security and abuse prevention | Legitimate interest | Audit log 7 years (FR-8). Tokens: until expiry plus cleanup (job in a later phase) |

IP addresses are never stored raw, only as an HMAC keyed by `SIGNING_SECRET`. The landing page sets no analytics or tracking cookies, so no cookie banner is needed yet.
