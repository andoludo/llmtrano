# One-shot model downloader. Models are data: they go to the `models` volume, not into an image.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

RUN pip install --no-cache-dir --root-user-action=ignore huggingface_hub==2.2.0

ENV HF_HUB_DISABLE_TELEMETRY=1
COPY docker/model-fetch/fetch.py /app/fetch.py
ENTRYPOINT ["python", "/app/fetch.py"]
