# Multi-stage: take the static pk-put client out of the perkeep image and run it
# alongside a small Starlette app in a slim python base. amd64-only (pk-put is an
# amd64 Go binary), so the compose service pins platform: linux/amd64.
FROM perkeep/perkeep@sha256:7b9de42049d7eeff4011e159fb30db7c0806cd406f45090e4f2ce5a419180e85 AS pk

FROM python:3.12.7-slim
COPY --from=pk /home/keepy/bin/pk-put /usr/local/bin/pk-put
COPY --from=pk /home/keepy/bin/pk-get /usr/local/bin/pk-get
RUN pip install --no-cache-dir \
    "starlette==1.3.1" \
    "uvicorn==0.49.0" \
    "python-multipart==0.0.32"
RUN mkdir -p /data
COPY app.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh
WORKDIR /app
ENV PERKEEP_SERVER=http://perkeep:3179 \
    CAMLI_AUTH=userpass:perkeep:perkeep \
    SHIM_SPOOL_DIR=/data
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
