FROM odoo:19.0

USER root

# Install ping utility for edge node network checks
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping && rm -rf /var/lib/apt/lists/*

# Copy custom addons and requirements
COPY ./crose /mnt/extra-addons/crose
COPY ./crose/requirements.txt /tmp/requirements.txt

# Install Python dependencies
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt


USER odoo
