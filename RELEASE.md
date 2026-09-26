<!--
  ~ Copyright (c) 2023-2026 Datalayer, Inc.
  ~
  ~ BSD 3-Clause License
-->

# Making a release

One tag releases both packages, with no stored token: PyPI and npm trust
`.github/workflows/release.yaml` through OIDC (trusted publishing).

| Package                 | Registry | Version from                  | GitHub environment |
| ----------------------- | -------- | ----------------------------- | ------------------ |
| `agent-teams`           | PyPI     | `agent_teams/__version__.py`  | `pypi`             |
| `@datalayer/agent-teams` | npm     | `package.json`                | `npm`              |

Both carry **one version**, the one the tag names.

## Steps

1. Bump the version in both files, on a branch, and open a pull request.
2. Merge, then tag the merge commit and push the tag:

   ```bash
   git checkout main && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

3. The `Release` workflow checks that the tag names both versions, builds the
   wheel, the sdist and the npm tarball, publishes what is not on the
   registries yet, and creates a GitHub release with generated notes.

## Trusted publishing

- PyPI project `agent-teams`: owner `datalayer`, repository `agent-teams`,
  workflow `release.yaml`, environment `pypi`.
- npm package `@datalayer/agent-teams`: GitHub Actions, organization
  `datalayer`, repository `agent-teams`, workflow filename `release.yaml`,
  environment `npm`. npm provenance also checks `package.json`'s
  `repository.url`, which names this repository.

The registry matches the repository, the workflow filename and the
environment exactly; renaming any of them means re-registering.
