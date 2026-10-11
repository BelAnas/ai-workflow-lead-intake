FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lead_intake lead_intake
COPY data data
ENV LLM_PROVIDER=mock LEADS_DB=/data/leads.db
RUN mkdir /data
EXPOSE 8024
CMD ["python", "-m", "lead_intake.cli", "serve", "--host", "0.0.0.0"]
