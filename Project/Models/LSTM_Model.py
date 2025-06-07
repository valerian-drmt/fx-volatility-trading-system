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
    def __init__(self, feature_dim, label_dim, hidden_dims, lookback, dropout):
        super().__init__()
        self.lookback = lookback
        self.feature_dim = feature_dim
        self.label_dim = label_dim
        self.hidden_dims = hidden_dims
        self.dropout = dropout
        self.l2_reg = tf.keras.regularizers.l2(1e-4)

        self.lstm_layers = []
        for i, units in enumerate(hidden_dims):
            return_seq = i < len(hidden_dims) - 1
            self.lstm_layers.append(
                tf.keras.layers.LSTM(
                    units,
                    return_sequences=return_seq,
                    dropout=dropout,
                    recurrent_dropout=dropout,
                    kernel_regularizer=self.l2_reg
                )
            )
            self.lstm_layers.append(tf.keras.layers.Dropout(dropout))

        self.dense = tf.keras.layers.Dense(
            label_dim,
            activation='sigmoid',
            kernel_regularizer=self.l2_reg
        )

    def call(self, inputs, training=False):
        x = inputs
        for layer in self.lstm_layers:
            x = layer(x, training=training)
        return self.dense(x)

    def build(self, input_shape):
        dummy_input = tf.zeros((1, input_shape[1], input_shape[2]))
        self.call(dummy_input)
        super().build(input_shape)

    def build_graph(self):
        x = tf.keras.Input(shape=(self.lookback, self.feature_dim))
        return tf.keras.Model(inputs=[x], outputs=self.call(x))