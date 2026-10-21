FROM python:3.12-slim
RUN pip install --no-cache-dir tzdata
WORKDIR /app
COPY app.py dashboard.html icon.png banner.jpg ./
ENV PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8099
CMD ["python", "app.py"]
