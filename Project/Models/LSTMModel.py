import tensorflow as tf
from tensorflow.keras.layers import LSTM

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

class LSTMModel(tf.keras.Model):
    def __init__(
        self,
        feature_dim,
        label_dim,
        hidden_dims,
        lookback,
        dropout,
        recurrent_dropout,
        activation,
        recurrent_activation,
        kernel_initializer,
        use_bias,
        bidirectional,
        use_batch_norm,
        dense_units,
        activation_dense,
        kernel_regularizer_l2,
        loss_function,
        metrics_list,
        learning_rate,
        clipnorm,
        beta_1,
        beta_2,
        epsilon,
        early_stopping_patience,
        early_stopping_min_delta,
        restore_best_weights,
        monitor_metric,
        use_reduce_lr,
        reduce_lr_patience,
        reduce_lr_factor,
        reduce_lr_min_lr,
        use_model_checkpoint,
        checkpoint_filepath
    ):
        super().__init__()
        self.lookback = lookback
        self.feature_dim = feature_dim
        self.label_dim = label_dim
        self.hidden_dims = hidden_dims
        self.dropout = dropout
        self.recurrent_dropout = recurrent_dropout
        self.activation = activation
        self.recurrent_activation = recurrent_activation
        self.kernel_initializer = kernel_initializer
        self.use_bias = use_bias
        self.bidirectional = bidirectional
        self.use_batch_norm = use_batch_norm
        self.dense_units = dense_units
        self.activation_dense = activation_dense
        self.kernel_regularizer = tf.keras.regularizers.l2(kernel_regularizer_l2)
        self.loss_function = loss_function
        self.metrics_list = metrics_list
        self.learning_rate = learning_rate
        self.clipnorm = clipnorm
        self.beta_1 = beta_1
        self.beta_2 = beta_2
        self.epsilon = epsilon
        self.early_stopping_patience = early_stopping_patience
        self.early_stopping_min_delta = early_stopping_min_delta
        self.restore_best_weights = restore_best_weights
        self.monitor_metric = monitor_metric
        self.use_reduce_lr = use_reduce_lr
        self.reduce_lr_patience = reduce_lr_patience
        self.reduce_lr_factor = reduce_lr_factor
        self.reduce_lr_min_lr = reduce_lr_min_lr
        self.use_model_checkpoint = use_model_checkpoint
        self.checkpoint_filepath = checkpoint_filepath

        self.lstm_layers = []
        for i, units in enumerate(hidden_dims):
            return_seq = i < len(hidden_dims) - 1
            lstm_layer = tf.keras.layers.LSTM(
                units,
                return_sequences=return_seq,
                dropout=self.dropout,
                recurrent_dropout=self.recurrent_dropout,
                activation=self.activation,
                recurrent_activation=self.recurrent_activation,
                kernel_initializer=self.kernel_initializer,
                kernel_regularizer=self.kernel_regularizer,
                use_bias=self.use_bias
            )
            if self.bidirectional:
                lstm_layer = tf.keras.layers.Bidirectional(lstm_layer)

            self.lstm_layers.append(lstm_layer)

            if self.use_batch_norm:
                self.lstm_layers.append(tf.keras.layers.BatchNormalization())

            self.lstm_layers.append(tf.keras.layers.Dropout(self.dropout))

        self.intermediate_dense = None
        if self.dense_units is not None:
            self.intermediate_dense = tf.keras.layers.Dense(
                self.dense_units,
                activation=self.activation_dense,
                kernel_regularizer=self.kernel_regularizer,
                use_bias=self.use_bias
            )

        self.output_dense = tf.keras.layers.Dense(
            label_dim,
            activation='sigmoid',
            kernel_regularizer=self.kernel_regularizer,
            use_bias=self.use_bias
        )

    def call(self, inputs, training=False):
        x = inputs
        for layer in self.lstm_layers:
            x = layer(x, training=training)
        if self.intermediate_dense is not None:
            x = self.intermediate_dense(x)
        return self.output_dense(x)

    def build(self, input_shape):
        dummy_input = tf.zeros((1, input_shape[1], input_shape[2]))
        self.call(dummy_input)
        super().build(input_shape)

    def build_graph(self):
        x = tf.keras.Input(shape=(self.lookback, self.feature_dim))
        return tf.keras.Model(inputs=[x], outputs=self.call(x))

    def compile_model(self):
        """Compile the model using internal attributes."""
        optimizer = self.get_optimizer()
        self.compile(
            optimizer=optimizer,
            loss=self.loss_function,
            metrics=self.metrics_list
        )

    def build_model(self):
        self.build((None, self.lookback, self.feature_dim))
        self.summary()

    def get_optimizer(self):
        return tf.keras.optimizers.Adam(
            learning_rate=self.learning_rate,
            clipnorm=self.clipnorm,
            beta_1=self.beta_1,
            beta_2=self.beta_2,
            epsilon=self.epsilon
        )

    def get_callbacks(self):
        callbacks = []

        early_stop = tf.keras.callbacks.EarlyStopping(
            patience=self.early_stopping_patience,
            min_delta=self.early_stopping_min_delta,
            restore_best_weights=self.restore_best_weights,
            monitor=self.monitor_metric
        )
        callbacks.append(early_stop)

        if self.use_reduce_lr:
            reduce_lr = tf.keras.callbacks.ReduceLROnPlateau(
                monitor=self.monitor_metric,
                factor=self.reduce_lr_factor,
                patience=self.reduce_lr_patience,
                min_lr=self.reduce_lr_min_lr
            )
            callbacks.append(reduce_lr)

        if self.use_model_checkpoint:
            checkpoint = tf.keras.callbacks.ModelCheckpoint(
                filepath=self.checkpoint_filepath,
                monitor=self.monitor_metric,
                save_best_only=True
            )
            callbacks.append(checkpoint)

        return callbacks

class F1Score(tf.keras.metrics.Metric):
    def __init__(self, name='f1_score', threshold=0.5, **kwargs):
        super().__init__(name=name, **kwargs)
        self.threshold = threshold
        self.precision = tf.keras.metrics.Precision(thresholds=self.threshold)
        self.recall = tf.keras.metrics.Recall(thresholds=self.threshold)

    def update_state(self, y_true, y_pred, sample_weight=None):
        self.precision.update_state(y_true, y_pred, sample_weight)
        self.recall.update_state(y_true, y_pred, sample_weight)

    def result(self):
        p = self.precision.result()
        r = self.recall.result()
        return 2 * (p * r) / (p + r + tf.keras.backend.epsilon())

    def reset_states(self):
        self.precision.reset_state()
        self.recall.reset_state()