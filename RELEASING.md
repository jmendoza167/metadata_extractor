# Releasing metaex

Releases are published only by GitHub Actions (`.github/workflows/release.yml`)
with PyPI Trusted Publishing: no API token is used anywhere. The one-time
PyPI and GitHub settings are listed in CLAUDE.md ("Releases and
publishing"). The commands below are for version **0.1.0**; change the
version everywhere for later releases.

## 1. Before tagging

1. `main` is green on GitHub (the `tests` and `secrets` workflows).
2. In a clean, up-to-date checkout of `main`, with the dev dependencies
   installed (`pip install -e ".[dev]"`):

   ```
   python -m pytest                     # must exit with 0
   git log -i --grep="co-authored-by" --oneline     # must print nothing
   gitleaks git --redact .              # must say "no leaks found"
   ```

3. Build and try the packages yourself (`dist/` is git-ignored):

   ```
   python -m build --outdir dist .
   python -m twine check --strict dist/*
   python scripts/check_wheel.py dist/metaex-0.1.0-py3-none-any.whl tests/samples
   ```

   `check_wheel.py` installs the wheel alone in a new virtual environment
   and strips and verifies samples with the installed `metaex`. Also run
   that installed `metaex` on a few of your own files in `private_samples/`
   (never commit them) and check the results by eye.
4. Write the release date into `CHANGELOG.md`: change
   `## [0.1.0] - Unreleased` to `## [0.1.0] - YYYY-MM-DD`, with the date you
   will push the tag (UTC). Leave the empty `## [Unreleased]` section above
   it. Commit this on `main` (for example `docs: release 0.1.0`), push, and
   wait for CI to pass. The release workflow refuses a tag whose CHANGELOG
   section still says "Unreleased".

## 2. Tag and push

```
git switch main
git pull --ff-only
git tag -a v0.1.0 -m "metaex 0.1.0"
git push origin v0.1.0
```

The tag must be exactly `v` + the version in `src/metaex/__init__.py`, on a
commit of `main`; the workflow checks both. It then runs the full test matrix, builds the packages
once, checks them on Windows, Linux and macOS, publishes them to PyPI (if
the `pypi` environment requires a reviewer, approve it under Actions) and
creates the GitHub Release with the CHANGELOG section as its notes and the
two files attached.

Afterwards, check <https://pypi.org/project/metaex/> and the release page,
and install it like a user would: `pipx install metaex==0.1.0` (or `pip
install metaex==0.1.0` in a new virtual environment), then
`metaex --version` and `metaex formats`.

If the workflow fails **before** publishing, fix the problem on `main`,
delete the tag and tag again:

```
git push --delete origin v0.1.0
git tag -d v0.1.0
```

Once a version is on PyPI it can never be uploaded again, even after it is
deleted. If a published release is broken, yank it on PyPI (Manage project
> Releases > Yank) and release 0.1.1.

## 3. After the release

New changes go under `## [Unreleased]` in `CHANGELOG.md`. For the next
release, set the new version in `src/metaex/__init__.py`, the
`## [X.Y.Z] - Unreleased` section and its link at the bottom of the
CHANGELOG, the supported versions in `SECURITY.md` (for a new minor
version) and the commands in this file.
