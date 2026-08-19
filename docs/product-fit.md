# Product fit and boundaries

Probezen catches changes in the observed behavior of external APIs, including required fields
disappearing, stable types changing, values becoming null, and historically nonempty arrays
becoming empty. It is most useful in applications that depend on APIs they do not control and want
an explicit, reviewable baseline checked in local development or CI.

## The problem it catches

An endpoint can continue returning a successful response while changing data your application
relies on:

```text
Yesterday:
{"product": {"price": 19.99, "currency": "USD"}}

Today:
{"product": {"price": "19.99"}}

HTTP status: 200 OK
Probezen:
  product.price changed from number to string
  product.currency is missing
```

Probezen learns consistent behavior from repeated responses, presents it for approval, and stores
the approved rules in `probezen.lock.json`. Later checks compare current responses with those rules
and identify the exact path and behavior that changed.

## A good fit

Probezen is a good fit when:

- your application consumes a JSON API maintained by another organization or team;
- a successful but structurally changed response could break application code;
- you can collect representative baseline responses safely;
- you want the observed contract to be explicit, reviewable, and committed with the consumer;
- you want deterministic checks that can run locally and in ordinary CI.

## Boundaries

Probezen currently checks JSON responses from HTTP GET endpoints. It detects supported structural
and operational drift, not changes in business meaning that preserve the same observable shape;
for example, it cannot decide whether `19.99` changing to `99.99` is correct.

Probezen does not host checks, manage credentials, generate broad request suites, or provide
regional availability alerting. Live checks use credentials supplied by your environment and
remain subject to the API's availability, rate limits, and natural response variability.

Probezen complements existing tests and monitoring. Its specific job is to make unexpected changes
in an external API's observed behavior visible before they silently reach users.
