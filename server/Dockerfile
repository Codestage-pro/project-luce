FROM python:3.13-slim
WORKDIR /app
COPY sfu_proxy.py .
# 0.0.0.0 só dentro do container: o compose publica a porta apenas em 127.0.0.1
# do host (quem alcança de fora é o cloudflared).
ENV LUCE_BIND=0.0.0.0 LUCE_PORT=8889 PYTHONUNBUFFERED=1
EXPOSE 8889
CMD ["python", "sfu_proxy.py"]
