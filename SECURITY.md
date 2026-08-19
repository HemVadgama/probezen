# Security policy

Please report vulnerabilities through GitHub's private vulnerability reporting feature for this repository. Do not open a public issue containing credentials, exploit details, or other sensitive data.

Probezen resolves environment-backed headers only at request time and does not store response
bodies. It stores HTTP metadata, structural path/type summaries, array lengths, and low-cardinality
string evidence used for enum baselines. Values at common credential paths and configured
`sensitive_paths` are excluded from that evidence.

Nevertheless, inspect generated configuration before sharing it, keep `.probezen/` private, and
rotate any credential that may have been exposed. Repository discovery never reads `.env`; only
the documented example/template variants are inspected, and only URL hostnames are persisted.

Only the latest released version receives security fixes.

Install official releases from the
[Probezen PyPI project](https://pypi.org/project/probezen/) in an isolated environment:

```bash
uv tool install probezen
```

`pipx install probezen` is also supported. Verify that package metadata links back to
`github.com/HemVadgama/probezen` before installing; source installations should use a reviewed tag
or commit rather than an untrusted fork.
