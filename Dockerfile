FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install -r requirements.txt

RUN apt update && apt install tzdata -y

ENV TZ="America/Sao_Paulo"
ENV PYTHONPATH=/app

CMD ["python3", "-m", "src.main"]
