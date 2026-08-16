# Security policy

Please report vulnerabilities through GitHub's private vulnerability reporting feature for this repository. Do not open a public issue containing credentials, exploit details, or other sensitive data.

Driftlock resolves environment-backed headers only at request time and does not store response bodies. Nevertheless, inspect configuration before sharing it, keep `.driftlock/` private, and rotate any credential that may have been exposed.

Only the latest released version receives security fixes.

