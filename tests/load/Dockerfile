# One replica of the load stand: the framework installed from this source tree
# the way an application installs it, plus uvicorn to serve it.
# bullseye rather than the newest base: the Docker this is run with here
# (20.10.8) refuses the clone3 call newer glibc starts threads with.
FROM python:3.12-slim-bullseye

WORKDIR /src
COPY . /src
RUN pip install --no-cache-dir "/src[postgres]" "uvicorn[standard]>=0.30"

WORKDIR /stand
COPY tests/load/stand_app.py /stand/stand_app.py
# An empty catalogue: the stand declares no plugins and no sections.
RUN mkdir -p /stand/config && echo '{"plugins": [], "modules": []}' > /stand/config/modules.json

EXPOSE 8000
CMD ["uvicorn", "stand_app:app", "--host", "0.0.0.0", "--port", "8000"]
