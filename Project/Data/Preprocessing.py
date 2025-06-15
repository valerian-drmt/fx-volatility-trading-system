import math
import psutil
import tensorflow as tf
import numpy as np

# 🔧 Config import
import os
from Project.Config.LoggerConfig import *
logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")

class Preprocessing:
    def __init__(self, train_test_data):
        self.train_test_data = train_test_data

        # create_train_test_data def
        self.x_train = None
        self.y_train = None
        self.x_test = None
        self.y_test = None

        # suggest_batch_size def
        self.batch_size = None
        self.report_lines = None

        # create_dataloaders def
        self.train_loader = None
        self.val_loader = None
        self.val_dataset = None

    def create_train_test_data(self, lookback, size_test_prct):
        try:
            logger.info("Starting creation of train/test data.")

            # 1. Select columns
            feature_cols = [col for col in self.train_test_data.columns if col.startswith("Feature")]
            label_cols = [col for col in self.train_test_data.columns if col.startswith("Label")]
            logger.info(f"Selected {len(feature_cols)} feature(s) and {len(label_cols)} label(s).")

            # 2. Convert to numpy
            features = self.train_test_data[feature_cols].values
            labels = self.train_test_data[label_cols].values

            # 3. Normalize features
            features = (features - features.min(axis=0)) / (features.max(axis=0) - features.min(axis=0))
            logger.info("Features normalized (min-max).")

            # 4. Stack features and labels
            data_all = np.hstack((features, labels))

            # 5. Create sequences
            data = []
            for index in range(len(data_all) - lookback):
                data.append(data_all[index: index + lookback])
            data = np.array(data)
            logger.info(f"Generated {len(data)} sequences of length {lookback}.")

            # 6. Split train/test
            test_set_size = int(np.round(size_test_prct * data.shape[0]))
            train_set_size = data.shape[0] - test_set_size
            logger.info(f"Train/Test split: {train_set_size} train / {test_set_size} test samples.")

            x_train = data[:train_set_size, :, :len(feature_cols)]
            y_train = data[:train_set_size, -1, len(feature_cols):]
            x_test = data[train_set_size:, :, :len(feature_cols)]
            y_test = data[train_set_size:, -1, len(feature_cols):]

            # 7. Convert to TensorFlow tensors
            x_train = tf.convert_to_tensor(x_train, dtype=tf.float32)
            y_train = tf.convert_to_tensor(y_train, dtype=tf.int32)
            x_test = tf.convert_to_tensor(x_test, dtype=tf.float32)
            y_test = tf.convert_to_tensor(y_test, dtype=tf.int32)

            self.x_train = x_train
            self.y_train = y_train
            self.x_test = x_test
            self.y_test = y_test

            logger.info("✅ Train/test tensors successfully created.")
            return self

        except Exception as e:
            logger.error(f"❌ Error during create_train_test_data: {e}", exc_info=True)
            raise

    def create_dataloaders(self, val_ratio):
        try:
            logger.info("Creating TensorFlow DataLoaders...")

            # ---- 1. Détermination de la taille de validation
            n_samples = self.x_train.shape[0]
            val_size = int(val_ratio * n_samples)
            train_size = n_samples - val_size
            logger.info(f"Train/Val split: {train_size} train / {val_size} val samples")

            # ---- 2. Mélange et split manuel
            indices = np.random.permutation(n_samples)
            train_indices = indices[:train_size]
            val_indices = indices[train_size:]

            x_train_split = tf.gather(self.x_train, train_indices)
            y_train_split = tf.gather(self.y_train, train_indices)
            x_val_split = tf.gather(self.x_train, val_indices)
            y_val_split = tf.gather(self.y_train, val_indices)

            # ---- 3. Création des tf.data.Dataset
            train_dataset = tf.data.Dataset.from_tensor_slices((x_train_split, y_train_split))
            val_dataset = tf.data.Dataset.from_tensor_slices((x_val_split, y_val_split))

            # ---- 4. Batching et options
            train_loader = train_dataset.shuffle(buffer_size=train_size).batch(self.batch_size).prefetch(tf.data.AUTOTUNE)
            val_loader = val_dataset.batch(self.batch_size).prefetch(tf.data.AUTOTUNE)

            logger.info("✅ TensorFlow DataLoaders created successfully.")

            self.train_loader = train_loader
            self.val_loader = val_loader
            self.val_dataset = val_dataset

            return self

        except Exception as e:
            logger.error(f"❌ Error while creating TensorFlow DataLoaders: {e}", exc_info=True)
            raise

    def suggest_batch_size(self, n_features, n_labels, lookback, reserved_ram_gb, hidden_dim):
        try:
            logger.info("Starting batch size estimation...")
            n_samples = self.x_train.shape[0]
            num_layers = len(hidden_dim)
            # ---- 1. RAM available on the machine
            total_ram_bytes = psutil.virtual_memory().total
            reserved_ram_bytes = reserved_ram_gb * 1024 ** 3
            usable_ram_bytes = max(total_ram_bytes - reserved_ram_bytes, 0)

            # ---- 2. Memory per sample
            bytes_per_element = 4  # float32
            input_output_elements = (lookback * n_features) + n_labels
            input_output_bytes = input_output_elements * bytes_per_element

            # ---- 3. LSTM hidden state memory estimation
            total_hidden_units = sum(hidden_dim)
            lstm_internal_bytes = total_hidden_units * lookback * bytes_per_element * num_layers

            # ---- 4. Total bytes per sample
            bytes_per_sample = input_output_bytes + lstm_internal_bytes

            # ---- 5. Estimate batch size from available RAM
            max_batch_size_ram = usable_ram_bytes // bytes_per_sample

            # ---- 6. CPU-based limit
            cpu_cores = os.cpu_count() or 2
            max_batch_size_cpu = cpu_cores * 8  # empirical limit

            # ---- 7. GPU or CPU constraints
            gpus = tf.config.list_physical_devices('GPU')
            has_gpu = len(gpus) > 0

            if has_gpu:
                max_batch_size = min(max_batch_size_ram, 1024)
            else:
                max_batch_size = min(max_batch_size_ram, max_batch_size_cpu)

            # ---- 8. Clamp & round
            max_batch_size = max(1, min(n_samples, int(max_batch_size)))
            batch_size = 2 ** int(math.log2(max_batch_size))

            # ---- 9. Summary
            report_lines = [
                "--- Batch Size Estimation Report (TensorFlow) ---",
                f"Total RAM (GB):         {total_ram_bytes / 1024 ** 3:.2f}",
                f"Reserved RAM (GB):      {reserved_ram_gb}",
                f"Usable RAM (GB):        {usable_ram_bytes / 1024 ** 3:.2f}",
                f"Samples (n_samples):    {n_samples}",
                f"Features per timestep:  {n_features}",
                f"Labels (n_labels):      {n_labels}",
                f"Lookback (timesteps):   {lookback}",
                f"Hidden dimensions:      {hidden_dim} (layers: {num_layers})",
                f"Bytes/sample:           {bytes_per_sample / 1024:.2f} KB",
                f"Max batch size (RAM):   {max_batch_size_ram}",
                f"Max batch size (CPU):   {max_batch_size_cpu}",
                f"Using GPU:              {'Yes' if has_gpu else 'No'}",
                f"Final suggested batch:  {batch_size}",
                "--------------------------------------------------"
            ]

            self.report_lines = report_lines
            self.batch_size = batch_size

            return self

        except Exception as e:
            logger.error(f"❌ Error during batch size estimation: {e}", exc_info=True)
            raise