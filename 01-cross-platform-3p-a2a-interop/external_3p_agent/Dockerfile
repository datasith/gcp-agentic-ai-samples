FROM python:3.11-slim

WORKDIR /app

# Run as a non-root user (compatible with Hugging Face Spaces UID 1000 and Cloud Run)
RUN useradd -m -u 1000 appuser

COPY --chown=appuser:appuser requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY --chown=appuser:appuser app.py /app/app.py

USER appuser

EXPOSE 7860

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
