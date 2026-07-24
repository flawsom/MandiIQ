FROM python:3.12-slim

WORKDIR /app

# Install from mandi_rdd/requirements.txt (includes Phase 11 AI deps)
COPY mandi_rdd/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY mandi_rdd/ /app/mandi_rdd/

ENV PYTHONPATH=/app
ENV PORT=8000

EXPOSE 8000

# Run the API server
CMD ["uvicorn", "mandi_rdd.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
