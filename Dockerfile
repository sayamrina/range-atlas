# One image for the API and the model worker (AWS Batch runs it with `python -m pipeline.worker <species>`).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pipeline pipeline
COPY api api
ENV RANGE_ATLAS_LAKE=/data/lake RANGE_ATLAS_CACHE=/data/cache
EXPOSE 8000
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
