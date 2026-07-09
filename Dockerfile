FROM mambaorg/micromamba:1.5.10

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        git \
        make \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

USER $MAMBA_USER
WORKDIR /workspace/OpenMC-Agent

COPY --chown=$MAMBA_USER:$MAMBA_USER environment.yml ./environment.yml
RUN micromamba env create -f environment.yml \
    && micromamba clean --all --yes

COPY --chown=$MAMBA_USER:$MAMBA_USER . .
RUN micromamba run -n openmc-env python -m pip install -e ".[dev]"

ENV ENV_NAME=openmc-env \
    OPENMC_AGENT_STREAM=1 \
    OPENMC_AGENT_LLM_HEARTBEAT_SECONDS=10 \
    PYTHONUNBUFFERED=1

SHELL ["/usr/local/bin/micromamba", "run", "-n", "openmc-env", "/bin/bash", "-lc"]
CMD ["/bin/bash"]
