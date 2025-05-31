# 🖥️ Start application
try:
    logger.info("Starting QApplication...")
    app = QApplication(sys.argv)

    window = MainWindow()
    logger.info("MainWindow initialized.")

    df = fetcher.raw_data[-3000:].copy()

    if 'timestamp' not in df.columns:
        logger.error("❌ 'timestamp' column missing from DataFrame.")
        sys.exit(1)

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    logger.info("✅ 'timestamp' column converted to datetime format.")

    window.load_and_plot(df)
    logger.info("Candlestick plot loaded into MainWindow.")

    window.show()
    logger.info("MainWindow shown. Entering application loop.")
    sys.exit(app.exec())

except Exception as e:
    logger.exception(f"❌ Application startup failed: {e}")
    sys.exit(1)

