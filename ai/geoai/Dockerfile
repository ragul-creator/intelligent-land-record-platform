FROM python:3.12-slim

WORKDIR /workspace
COPY ai/geoai/requirements.txt /tmp/geoai-requirements.txt
RUN pip install --no-cache-dir -r /tmp/geoai-requirements.txt
COPY ai /workspace/ai
ENV PYTHONPATH=/workspace
ENTRYPOINT ["python", "-m", "ai.geoai.cli"]
