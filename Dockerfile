FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY src ./src
COPY prompts ./prompts
COPY docs ./docs
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 docagent
USER docagent
EXPOSE 8000
CMD ["uvicorn", "docagent.main:app", "--host", "0.0.0.0", "--port", "8000"]
