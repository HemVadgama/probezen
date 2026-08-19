# Authenticated API example

Probezen stores an environment-variable reference, never the resolved credential. Put the complete
header value in your environment:

```bash
export API_AUTH_HEADER="Bearer replace-with-a-real-token"

probezen init
probezen add account https://api.example.com/v1/account \
  --header-env Authorization=API_AUTH_HEADER \
  --header Accept=application/json
probezen doctor
probezen learn account
probezen check account
```

The generated configuration is safe to commit:

```yaml
headers:
  Accept: application/json
  Authorization:
    env: API_AUTH_HEADER
```

Do not put literal credentials in `--header`, `probezen.yml`, or the lock file. In CI, map
`API_AUTH_HEADER` from the CI system's secret store.
