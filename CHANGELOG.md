# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## v0.1.0 - 2026-10-21

### Added

- First tagged release of the current `main` tip (`fde7f74`).
- FastAPI backend for PredixLab with APIs for authentication, market data reads,
  picks, analysis, watchlists, and subscriptions.
- Reads data from MongoDB and read-only file snapshots produced by Collector.
  This repository does not train models.
