import tensorflow as tf
from tensorflow.keras.layers import LSTM, Dense

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

class LSTM_Model(tf.keras.Model):
    def __init__(self, feature_dim, label_dim, hidden_dim, lookback):
        super(LSTM_Model, self).__init__()

        self.lookback = lookback
        self.feature_dim = feature_dim

        self.lstm = LSTM(hidden_dim, return_sequences=False, input_shape=(lookback, feature_dim))
        self.dense = Dense(label_dim, activation='sigmoid')  # For multi-label binary classification

    def call(self, inputs, training=False):
        x = self.lstm(inputs)
        return self.dense(x)

    def build_graph(self):
        x = tf.keras.Input(shape=(self.lookback, self.feature_dim))
        return tf.keras.Model(inputs=[x], outputs=self.call(x))