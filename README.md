# Kalshi Weather Trading Bot

Automated weather prediction market trading system. Pulls ECMWF ensemble
forecasts (Open-Meteo, free/no key), compares them to Kalshi weather
temperature markets, and paper-trades (eventually live-trades) any edge
that clears strict risk guardrails. All state lives in a local SQLite
database and terminal/log output — no frontend.
