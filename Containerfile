FROM python:3.13-slim

WORKDIR /opt/werewolf

COPY pyproject.toml .
COPY src/ src/
COPY static/ static/
RUN pip install --no-cache-dir .

ENV PYTHONPATH=/opt/werewolf/src
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--ws-ping-interval", "10", "--ws-ping-timeout", "10"]
