# GitHub Marketplace release guide

Probezen ships one root `action.yml` named **Probezen Dependency Reliability**. Its Marketplace
positioning is:

> **Catch breaking API changes before your users do.**

> Probezen watches the APIs and external services your application depends on, detects meaningful
> behavioral changes, and shows you where your code may be affected.

The repository is intentionally both the Probezen CLI and Action distribution unit: the composite
Action installs the package from its immutable checked-out release, so Action behavior and CLI
behavior cannot silently diverge.

## Publication checklist

Before publishing:

1. Confirm the repository is public and contains exactly one root `action.yml`/`action.yaml`.
2. Confirm the Action name remains available and unique in GitHub Marketplace.
3. Run the full CI and GitHub Action workflows on GitHub-hosted runners.
4. Confirm `action.yml` shows GitHub's “Everything looks good” Marketplace validation.
5. Review the README inputs, outputs, secret handling, permissions, and scheduled example.
6. Build and test the `probezen` wheel and source distribution.
7. Create an immutable release tag, currently `v0.2.1`.
8. Point the compatible major tag `v1` at the same tested commit.
9. Draft the GitHub release from `v0.2.1`, select **Publish this Action to the GitHub
   Marketplace**, and choose the most relevant categories.
10. Accept the GitHub Marketplace Developer Agreement if GitHub prompts for it, then publish with
    two-factor authentication.

Recommended tag commands after the release commit is on `main`:

```bash
git tag -a v0.2.1 -m "Probezen v0.2.1"
git tag -f v1 v0.2.1
git push origin v0.2.1
git push origin v1 --force
```

The immutable version tag supports reproducible pinning. The moving `v1` tag delivers compatible
security and bug fixes to users who select the stable major channel. Never move `v1` to a breaking
Action interface.

## Permissions and secrets

The Action metadata requests no GitHub token and cannot grant itself repository permissions.
Published examples use only:

```yaml
permissions:
  contents: read
```

Probezen reads the checked-out workspace and configured HTTP credentials only. A caller must map
any endpoint secret explicitly through `env`; the Action neither reads all repository secrets nor
sends credentials anywhere except the endpoint configured by that repository. JSON artifacts are
opt-in and contain findings plus metadata, not raw API response bodies or authorization headers.

## Release verification

The `GitHub Action` workflow exercises two adoption states on `ubuntu-latest`:

- a configured project running `doctor` with structured outputs;
- a first-run project using the default `check` command and receiving `setup-required` guidance.

The Python test suite additionally covers threshold behavior, annotation escaping, Markdown
sanitization, baseline-required behavior, network failures, output files, custom configuration
paths, and an end-to-end first-run subprocess.

Marketplace publication itself is a repository-owner operation in GitHub's release UI; code and
metadata cannot accept the Marketplace agreement or select listing categories automatically.
