# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

FROM python:3.10-slim

WORKDIR /app

COPY pyproject.toml pyproject.toml
COPY LICENSE LICENSE
COPY README.md README.md
COPY agent_teams/* agent_teams/

RUN pip install -e .

RUN pip uninstall -y pycrdt datalayer_pycrdt
RUN pip install datalayer_pycrdt==0.12.17

CMD ["uvicorn", "--factory", "agent_teams.app:create_app", "--host", "0.0.0.0", "--port", "8765"]
