Severity (how much harm a problem causes). Severity is based on impact, not on how complicated the code looks, and every finding gives the reason for its severity:

- critical: Immediate, severe impact, such as data exposure, a major security compromise or widespread data loss. Example: an authentication bypass that exposes sensitive records.
- high: A major functional failure affecting an important workflow. Example: payment or claim processing fails for valid requests.
- medium: A real problem affecting a limited scenario or edge case, or one that makes the code clearly harder to maintain. Example: missing handling for a particular optional field; logic copied instead of reused.
- low: Minor impact that doesn't break the main workflow. Example: a non-critical error message is misleading.
