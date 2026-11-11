# Airflow with the libraries of the pipeline, at the exact versions of requirements.txt (the same == pins as the command
# line, RNF01). The official pattern: reinstall apache-airflow at the same version so that pip never changes
# it while it resolves the other packages; if a pin ever conflicts with Airflow's own constraints the build fails
# instead of installing a different version. The build context is the project folder (see docker-compose.yaml) and
# .dockerignore lets only requirements.txt into it.
# The base image is pinned by its version and by its digest (the one project 02 pinned, decision D23): the tag alone can be moved to other bytes.
FROM apache/airflow:3.4.0@sha256:a8d9d19c563c768b2bc605ef26440f28a9164ea886064e99c599b057432ff572

ARG AIRFLOW_VERSION=3.4.0
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /requirements.txt
