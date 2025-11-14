# Trading Project

A professional, end-to-end pipeline to fetch market data, engineer features, and train LSTM models for multi-label trading signals.

## Purpose

Research and build a robust, reproducible pipeline for:
- Ingesting market data from exchanges and brokers
- Preprocessing and feature/label engineering for time-series data
- Training and evaluating LSTM-based models for trading signal generation

## Project Structure

Typical `src/` layout (guideline):
- core/config/…: logging, settings, constants
- core/data/…: data fetching, loading/saving, DOM→OHLCV transforms
- core/features/…: feature and label computation
- core/preprocessing/…: normalization, windowing, splits, DataLoaders
- core/models/…: PyTorch LSTM and heads
- core/training/…: trainers, metrics, checkpointing, schedulers
- utils/…: helpers (paths, plotting, monitoring)

## Key Capabilities

- Data
  - Fetch OHLCV from exchanges
  - Depth-of-market (DOM) handling and conversion to OHLCV
  - Broker tick data collection (async-friendly)
  - Save/load datasets for reproducibility

- Preprocessing
  - Lookback-based sequence building
  - Train/val/test splits
  - Normalization/scaling
  - PyTorch DataLoaders
  - Batch size suggestion based on RAM/model size

- Modeling
  - LSTM for multi-label classification
  - Class imbalance handling (e.g., pos_weight)
  - Optimizer + LR scheduling
  - Early stopping and best-model checkpointing
  - Metrics: F1, Precision, Recall, AUC, Exact Match, Hamming Loss

