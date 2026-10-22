#!/bin/sh
# Creates the throwaway config files the tests expect under /tmp/dt (never touches a real .env).
mkdir -p /tmp/dt/data
cat > /tmp/dt/.env << 'X'
# test env
TZ=America/Phoenix
NTFY_TOPIC=secret-topic-123
# GAME_MON=
# GAME_TUE=
DASHBOARD_PASSWORD=pw-test
ACK_TOKEN=tok123
ACK_BASE_URL=http://127.0.0.1:8099
GAME_WED=Trivia
X
cat > /tmp/dt/envA << 'X'
TZ=America/Phoenix
NTFY_TOPIC=abc
GAME_WED=Trivia
X
cat > /tmp/dt/envC.orig << 'X'
# test env
TZ=America/Phoenix
NTFY_TOPIC=abc
DASHBOARD_PASSWORD=pw
GAME_MON=Trivia
X
cp /tmp/dt/envC.orig /tmp/dt/envC
printf 'TZ=America/Phoenix\nNTFY_TOPIC=abc\n' > /tmp/dt/envS
