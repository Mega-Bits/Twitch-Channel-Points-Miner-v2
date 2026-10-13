FROM python:3.10-bookworm

WORKDIR /usr/src/app

COPY ./requirements.txt ./

# Pillow needs C headers on 32-bit ARM, but the miner doesn't need a native
# pandas/numpy build, legacy cryptography 3.3.2, or OS-wide build toolchains.
# Retain the libjpeg runtime library after removing development packages.
RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       gcc make patch zlib1g-dev libjpeg-dev libjpeg62-turbo \
  && python -m pip install --no-cache-dir -r requirements.txt \
  && apt-get purge -y --auto-remove gcc make zlib1g-dev libjpeg-dev \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

ADD ./TwitchChannelPointsMiner ./TwitchChannelPointsMiner
COPY ./patches/drop-campaign-state-machine.patch /tmp/drop-campaign-state-machine.patch
COPY ./patches/drop-campaign-single-progress.patch /tmp/drop-campaign-single-progress.patch
RUN patch --batch --forward -p1 < /tmp/drop-campaign-state-machine.patch \
  && patch --batch --forward -p1 < /tmp/drop-campaign-single-progress.patch \
  && python -m compileall -q TwitchChannelPointsMiner \
  && rm -f /tmp/drop-campaign-state-machine.patch \
  && rm -f /tmp/drop-campaign-single-progress.patch

ENTRYPOINT [ "python", "run.py" ]
