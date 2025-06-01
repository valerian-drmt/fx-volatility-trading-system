import math
import psutil
import tensorflow as tf
import logging
import numpy as np

# 🔧 Config import
import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.append(project_root)
from Config.LoggerConfig import colored_logger
logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")

class Preprocessing:
    def __init__(self,train_test_data):
        self.data = train_test_data

        self.batch_size = None

        self.train_loader = None
        self.val_loader = None
        self.batch_size = None
        self.val_dataset = None

    def create_train_test_data(train_test_data, lookback, size_test_prct):
        # 1. Select columns
        feature_cols = [col for col in train_test_data.columns if col.startswith("Feature")]
        label_cols = [col for col in train_test_data.columns if col.startswith("Label")]

        # 2. Convert to numpy
        features = train_test_data[feature_cols].values
        labels = train_test_data[label_cols].values

        # 3. Normalize features
        features = (features - features.min(axis=0)) / (features.max(axis=0) - features.min(axis=0))

        # 4. Stack features and labels
        data_all = np.hstack((features, labels))

        # 5. Create sequences
        data = []
        for index in range(len(data_all) - lookback):
            data.append(data_all[index: index + lookback])
        data = np.array(data)

        # 6. Split train/test
        test_set_size = int(np.round(size_test_prct * data.shape[0]))
        train_set_size = data.shape[0] - test_set_size

        x_train = data[:train_set_size, :, :len(feature_cols)]
        y_train = data[:train_set_size, -1, len(feature_cols):]
        x_test = data[train_set_size:, :, :len(feature_cols)]
        y_test = data[train_set_size:, -1, len(feature_cols):]

        # 7. Convert to PyTorch tensors
        x_train = torch.from_numpy(x_train).float()
        y_train = torch.from_numpy(y_train).long()
        x_test = torch.from_numpy(x_test).float()
        y_test = torch.from_numpy(y_test).long()

        return x_train, y_train, x_test, y_test

    def split_data(self, train_test_data, lookback, size_test_prct):
        # 1. Save input
        self.train_test_data = train_test_data

        # 2. Split automatically: features = columns starting with "Feature_", labels = columns starting with "Label_"
        feature_cols = [col for col in train_test_data.columns if col.startswith("Feature")]
        label_cols = [col for col in train_test_data.columns if col.startswith("Label")]

        # 3. Convert to numpy arrays
        features = train_test_data[feature_cols].values
        labels = train_test_data[label_cols].values

        # 4. Normalize features
        features = (features - features.min(axis=0)) / (features.max(axis=0) - features.min(axis=0))

        # 5. Stack features and labels
        data_all = np.hstack((features, labels))

        # 6. Create sequences
        data = []
        for index in range(len(data_all) - lookback):
            data.append(data_all[index: index + lookback])
        data = np.array(data)

        # 7. Split train/test
        test_set_size = int(np.round(size_test_prct * data.shape[0]))
        train_set_size = data.shape[0] - test_set_size

        x_train = data[:train_set_size, :, :len(feature_cols)]
        y_train = data[:train_set_size, -1, len(feature_cols):]
        x_test = data[train_set_size:, :, :len(feature_cols)]
        y_test = data[train_set_size:, -1, len(feature_cols):]

        # 8. Convert to PyTorch tensors
        self.x_train = torch.from_numpy(x_train).float()
        self.y_train = torch.from_numpy(y_train).long()
        self.x_test = torch.from_numpy(x_test).float()
        self.y_test = torch.from_numpy(y_test).long()

        return self  # for chaining

    def create_dataloaders(self, x_train, y_train, val_ratio, feature_dim, n_labels, lookback,
                           reserved_ram_gb, hidden_dim, num_layers):
        try:
            logger.info("Creating TensorFlow DataLoaders...")

            # ---- 1. Détermination de la taille de validation
            n_samples = x_train.shape[0]
            val_size = int(val_ratio * n_samples)
            train_size = n_samples - val_size
            logger.info(f"Train/Val split: {train_size} train / {val_size} val samples")

            # ---- 2. Calcul du batch size optimal
            batch_size = self.suggest_batch_size(
                n_samples, feature_dim, n_labels, lookback,
                reserved_ram_gb, hidden_dim, num_layers
            )
            logger.info(f"Suggested batch size: {batch_size}")

            # ---- 3. Mélange et split manuel
            indices = np.random.permutation(n_samples)
            train_indices = indices[:train_size]
            val_indices = indices[train_size:]

            x_train_split = tf.gather(x_train, train_indices)
            y_train_split = tf.gather(y_train, train_indices)
            x_val_split = tf.gather(x_train, val_indices)
            y_val_split = tf.gather(y_train, val_indices)

            # ---- 4. Création des tf.data.Dataset
            train_dataset = tf.data.Dataset.from_tensor_slices((x_train_split, y_train_split))
            val_dataset = tf.data.Dataset.from_tensor_slices((x_val_split, y_val_split))

            # ---- 5. Batching et options
            train_loader = train_dataset.shuffle(buffer_size=train_size).batch(batch_size).prefetch(tf.data.AUTOTUNE)
            val_loader = val_dataset.batch(batch_size).prefetch(tf.data.AUTOTUNE)

            logger.info("✅ TensorFlow DataLoaders created successfully.")

            self.train_loader = train_loader
            self.val_loader = val_loader
            self.batch_size = batch_size
            self.val_dataset = val_dataset

            return self

        except Exception as e:
            logger.error(f"❌ Error while creating TensorFlow DataLoaders: {e}", exc_info=True)
            raise

    def suggest_batch_size(self,n_samples, n_features, n_labels, lookback, reserved_ram_gb, hidden_dim, num_layers):
        try:
            logger.info("Starting batch size estimation...")

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

            # ---- 9. Log summary
            logger.info("--- Batch Size Estimation Report (TensorFlow) ---")
            logger.info(f"Total RAM (GB):         {total_ram_bytes / 1024 ** 3:.2f}")
            logger.info(f"Reserved RAM (GB):      {reserved_ram_gb}")
            logger.info(f"Usable RAM (GB):        {usable_ram_bytes / 1024 ** 3:.2f}")
            logger.info(f"Samples (n_samples):    {n_samples}")
            logger.info(f"Features per timestep:  {n_features}")
            logger.info(f"Labels (n_labels):      {n_labels}")
            logger.info(f"Lookback (timesteps):   {lookback}")
            logger.info(f"Hidden dimensions:      {hidden_dim} (layers: {num_layers})")
            logger.info(f"Bytes/sample:           {bytes_per_sample / 1024:.2f} KB")
            logger.info(f"Max batch size (RAM):   {max_batch_size_ram}")
            logger.info(f"Max batch size (CPU):   {max_batch_size_cpu}")
            logger.info(f"Using GPU:              {'Yes' if has_gpu else 'No'}")
            logger.info(f"Final suggested batch:  {batch_size}")
            logger.info("--------------------------------------------------")

            self.batch_size = batch_size

            return self

        except Exception as e:
            logger.error(f"❌ Error during batch size estimation: {e}", exc_info=True)
            raise