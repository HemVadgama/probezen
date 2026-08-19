# Public API example

This example monitors one public JSON endpoint without authentication:

```bash
mkdir probezen-example && cd probezen-example
probezen init
probezen add todo https://jsonplaceholder.typicode.com/todos/1
probezen learn todo
probezen show todo
probezen check todo
```

Review the candidates before answering the `learn` approval prompt. Commit `probezen.yml` and
`probezen.lock.json`; do not commit `.probezen/`.

This public service is suitable for trying the workflow, not as a production dependency or an
availability guarantee. Live requests remain subject to the service's rate limits and uptime.
