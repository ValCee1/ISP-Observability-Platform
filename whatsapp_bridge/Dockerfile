# Standard-library-only service: no pip install step, so nothing to pin.
FROM python:3.12-slim
WORKDIR /app
COPY bridge.py .
# Budget counters live here (a named volume) so a restart can't reset caps.
RUN mkdir -p /var/lib/whatsapp-bridge && chown nobody /var/lib/whatsapp-bridge
USER nobody
EXPOSE 8080
CMD ["python", "-u", "bridge.py"]
