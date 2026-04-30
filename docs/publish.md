# Publishing Guide

Step-by-step for first publish and subsequent releases.

---

## One-time setup

### 1. Remove the private classifier

`pyproject.toml` contains `"Private :: Do Not Upload"` which blocks PyPI upload.
Remove it before publishing:

```toml
# pyproject.toml — delete this line:
"Private :: Do Not Upload",
```

Also set the correct development status classifier, e.g.:

```toml
"Development Status :: 5 - Production/Stable",
```

### 2. Configure PyPI trusted publisher (OIDC)

No API token needed — the publish workflow uses OIDC.

1. Log in to [pypi.org](https://pypi.org) → Account → Publishing → **Add a new pending publisher**
2. Fill in:
   - **PyPI project name**: `CoBrite` (must match `name` in `pyproject.toml`)
   - **Owner**: `ID-Photonics-GmbH`
   - **Repository**: `cobrite-py`
   - **Workflow filename**: `publish.yml`
   - **Environment name**: *(leave blank)*
3. Save. PyPI will accept pushes from this workflow without a token.

### 3. Enable GitHub Pages

1. Go to repo → **Settings** → **Pages**
2. Under **Source**, select **GitHub Actions**
3. Save.

### 4. Add the docs deploy workflow

Create `.github/workflows/docs.yml`:

```yaml
name: Deploy Docs

on:
  push:
    branches: ["main"]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0

      - uses: astral-sh/setup-uv@v7
        with:
          version: "0.10.2"
          enable-cache: true
          python-version: "3.12"

      - name: Set up Python
        run: uv python install

      - name: Install dependencies
        run: make install

      - name: Build docs
        run: make docs

      - uses: actions/upload-pages-artifact@v3
        with:
          path: site/

      - uses: actions/deploy-pages@v4
        id: deployment
```

Commit and push — docs will deploy automatically on every push to `main`.

---

## Releasing a new version

Versioning is driven by git tags via `uv-dynamic-versioning`. No manual version bumps needed.

### 1. Ensure main is clean and CI passes

```bash
git checkout main
git pull
```

Check that the CI workflow is green on GitHub.

### 2. Tag the release

```bash
git tag v1.0.0
git push origin v1.0.0
```

Tag format: `vMAJOR.MINOR.PATCH` (PEP 440 compatible).

### 3. Create a GitHub release

1. Go to repo → **Releases** → **Draft a new release**
2. Choose the tag you just pushed (`v1.0.0`)
3. Write release notes
4. Click **Publish release**

Publishing the release triggers `.github/workflows/publish.yml`, which:
- Installs deps (`make install`)
- Runs tests (`make test`)
- Builds sdist + wheel (`make build`)
- Publishes to PyPI via OIDC (`uv publish --trusted-publishing always`)

### 4. Verify

- PyPI: `https://pypi.org/project/CoBrite/`
- Docs: `https://id-photonics.github.io/cobrite/`

---

## Checklist for first publish

- [ ] Setup github pages -> cobrite.id-photonics.com
- [ ] Remove `"Private :: Do Not Upload"` from `pyproject.toml`
- [ ] Set correct development status classifier
- [ ] PyPI trusted publisher configured
- [ ] GitHub Pages source set to GitHub Actions
- [ ] `docs.yml` workflow added and pushed
- [ ] CI green on `main`
- [ ] Tag pushed and GitHub release published
- [ ] delete publish.md
