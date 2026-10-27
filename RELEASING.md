# Releasing

Releases are built and published by GitHub Actions ([release.yml](.github/workflows/release.yml))
with PyPI *trusted publishing*: PyPI trusts this repository's workflow, so no token is stored
anywhere. Every release is built with the canvas inside, installed in a clean environment and
smoke-tested ([tools/smoke_test.sh](tools/smoke_test.sh)) before anything is published.

## One-time setup (done by a maintainer, in a browser)

1. **PyPI**: sign in at <https://pypi.org> with two-factor authentication on. Open the `murakumo`
   project, **Manage → Publishing → Add a new publisher → GitHub**, and enter:
   owner `murakumo-data`, repository `murakumo`, workflow `release.yml`, environment `pypi`.
2. **TestPyPI**: the same at <https://test.pypi.org> (a separate account). If the project does
   not exist there yet, use **Your account → Publishing → Add a pending publisher**, with project
   name `murakumo` and environment `testpypi`.
3. **GitHub**: in the repository, **Settings → Environments**, create `pypi` and `testpypi`. On
   `pypi`, add yourself under **Required reviewers**, so every real release waits for your
   approval.

## Each release

1. Set the version in `packages/python/pyproject.toml`, add the release to
   [CHANGELOG.md](CHANGELOG.md), and merge.
2. Run the [end-to-end test](docs/end-to-end-test.md) on `main`.
3. Optional dry run: **Actions → Release → Run workflow**. It builds, smoke-tests and publishes to
   TestPyPI. Try it in a fresh virtual environment, taking only Murakumo from TestPyPI and its
   dependencies from PyPI (anyone can upload to TestPyPI, and it holds broken copies of some
   packages, such as `fastapi`):

   ```bash
   pip install --no-deps -i https://test.pypi.org/simple/ "murakumo==0.1.0"
   pip install "murakumo[canvas,verify,duckdb]==0.1.0"
   ```

   TestPyPI also never accepts the same version twice, so a second dry run of the same version
   fails at the upload step; that is expected.

4. Tag and push; the tag must match the version, or the workflow stops:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

5. Approve the `pypi` deployment in the workflow run. Then create a GitHub release from the tag
   with the changelog entry.

A version can never be uploaded twice to PyPI, even after deleting it: to fix a bad release,
publish the next patch version.
