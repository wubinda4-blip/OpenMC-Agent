# Cloud Environment Setup

This project can be run in a reproducible cloud development container with Conda, OpenMC, and the Python development dependencies preinstalled.

## Options

### Dev Container / Codespaces

1. Open the repository in a Dev Container-compatible cloud IDE.
2. Rebuild the container from `.devcontainer/devcontainer.json`.
3. Provide required secrets through the cloud IDE secret manager rather than committing them to the repository:
   - `ZHIPUAI_API_KEY`
   - `DEEPSEEK_API_KEY`
   - `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
   - `OPENMC_CROSS_SECTIONS` when a custom nuclear data library is mounted
4. Verify the base tooling without running OpenMC-heavy tests:

```bash
make check-env
make test-no-openmc
```

5. When the container includes OpenMC and the required runtime data, run OpenMC-gated checks explicitly:

```bash
make check-env-openmc
make test-openmc
```

### Docker

Build and test the image locally or in a cloud runner:

```bash
docker build -t openmc-agent .
docker run --rm \
  -e ZHIPUAI_API_KEY \
  -e DEEPSEEK_API_KEY \
  -e OPENAI_API_KEY \
  -e ANTHROPIC_API_KEY \
  -e OPENMC_CROSS_SECTIONS \
  openmc-agent \
  micromamba run -n openmc-env make test-all
```

For runners without OpenMC, use `make test-no-openmc` instead of `make test-openmc`.

### Conda-only runners

For CI systems or managed notebooks that already provide Conda/Mamba:

```bash
micromamba env create -f environment.yml
micromamba run -n openmc-env python -m pip install -e ".[dev]"
micromamba run -n openmc-env make check-env-openmc
micromamba run -n openmc-env make test-openmc
```

## Notes

- API keys and cross-section paths are runtime configuration, not repository state.
- The container sets safe defaults for streaming model output, but does not include any secrets.
- OpenMC cross-section data can be mounted into the container and exposed with `OPENMC_CROSS_SECTIONS`.

## Test layers

- Base Python / no OpenMC: `make test-no-openmc`. Missing OpenMC is allowed here.
- Conda / micromamba / Dev Container with OpenMC: `make check-env-openmc` and `make test-openmc`.
- Full validation: `make test-all` in an environment where OpenMC and runtime data are available.

CI or cloud runners that do not install OpenMC should not run `make test-openmc`.
