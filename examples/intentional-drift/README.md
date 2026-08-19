# Intentional drift example

When `probezen check vendor` reports a legitimate provider change, inspect it before accepting it:

```bash
probezen check vendor
probezen show vendor
probezen update vendor
git diff -- probezen.lock.json
probezen check vendor
```

`update` fetches the current response, displays its drift, and asks for confirmation. After
confirmation it collects fresh evidence and replaces only `vendor`'s approved contract. Use
`--count N` to control the evidence count and `--yes` only in a separately reviewed,
noninteractive process.

Commit the lock-file change only after its diff matches the provider change you intended to
accept. Probezen never updates a contract during `check`.
